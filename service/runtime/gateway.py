"""Real HTTP client for Marketing OS gateway integration (CS-B5).

Communicates with Marketing OS over HTTP using bearer service authentication
or per-request credentials. Isolated in service.runtime to preserve CS-B2
hygiene invariants (zero network calls in brand domain code).
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from service import assertion as _assertion
from service import jcs as _jcs
from service.brand.gateway import GatewayUnavailable

ASSERTION_HEADER = "X-CS-Service-Assertion"
MOS_AUDIENCE = "marketing-os"
ASSERTION_TTL_SECONDS = 60


class GatewayRefused(GatewayUnavailable):
    """Marketing OS answered with a definite 4xx: not retryable, never delivery."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.refused_status = status


class HttpMarketingOsGateway:
    """Real HTTP client for the Marketing OS gateway."""

    def __init__(self, base_url: str, service_token: str | None = None, opener=None,
                 assertion_key: str | None = None, clock=time.time):
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.assertion_key = assertion_key
        self._clock = clock
        self._open = opener or urllib.request.urlopen

    @staticmethod
    def service_target(url: str) -> str:
        """Path + query sorted by (key, value), exactly as MOS ``serviceTarget`` rebuilds it for the ``pth`` claim."""
        parts = urllib.parse.urlsplit(url)
        query = sorted(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        return parts.path + ("?" + urllib.parse.urlencode(query) if query else "")

    def _signed_headers(self, *, method: str, url: str, body: bytes, act: str, user_id: str | None,
                        mos_workspace_id: str, key: str | None = None) -> dict[str, str]:
        """Bearer (if any) plus, when a key and requester exist, the signed MOS service assertion. MOS authorizes the
        requester's current workspace role server-side; the assertion binds method+path, body hash and a single-use
        nonce so it cannot be replayed against another action, workspace or request."""
        headers = self._headers()
        key = key or self.assertion_key
        if key and user_id:
            # The signed assertion IS the authentication. Marketing OS treats any Authorization bearer as a user
            # session and refuses a service token with 401 before reading the assertion, so never send both.
            headers.pop("Authorization", None)
            now = int(self._clock())
            headers[ASSERTION_HEADER] = _assertion.sign(key, {
                "aud": MOS_AUDIENCE, "sub": user_id, "act": act, "bnd": mos_workspace_id,
                "bsha": hashlib.sha256(body).hexdigest(), "nonce": _assertion.b64e(secrets.token_bytes(24)),
                "iat": now, "exp": now + ASSERTION_TTL_SECONDS,
                "pth": f"{method.upper()} {self.service_target(url)}"})
        return headers

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.service_token:
            h["Authorization"] = f"Bearer {self.service_token}"
        return h

    def authorize_binding(self, *, user_id: str, mos_workspace_id: str) -> dict:
        url = f"{self.base_url}/api/creative-studio?action=authorize&workspace_id={mos_workspace_id}"
        req = urllib.request.Request(url, method="GET", headers=self._signed_headers(
            method="GET", url=url, body=b"", act="connection.authorize", user_id=user_id,
            mos_workspace_id=mos_workspace_id))
        try:
            with self._open(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                allowed = data.get("allowed", True)
                return {
                    "authority": "marketing-os",
                    "external_workspace_id": mos_workspace_id,
                    "subject_user_id": user_id,
                    "decision": "allowed" if allowed else "refused",
                    "role": data.get("role", "owner"),
                    "checked_at": datetime.now(timezone.utc).isoformat()
                }
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GatewayUnavailable(f"failed to authorize binding: {exc}") from None

    def export_brand_kit(self, *, mos_workspace_id: str, artifact_id: str, user_id: str | None = None) -> dict:
        url = f"{self.base_url}/api/brand-studio?action=export&workspace_id={mos_workspace_id}&artifact_id={artifact_id}"
        req = urllib.request.Request(url, method="GET", headers=self._signed_headers(
            method="GET", url=url, body=b"", act="brand.export", user_id=user_id, mos_workspace_id=mos_workspace_id))
        try:
            with self._open(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GatewayUnavailable(f"failed to export brand kit: {exc}") from None

    def fetch_asset(self, *, mos_workspace_id: str, ref: dict, purpose: str, max_bytes: int,
                    user_id: str | None = None) -> bytes:
        if self.assertion_key and user_id:
            # MOS serves exact brand-file bytes only to a signed, rights-checked, workspace-scoped request.
            url = f"{self.base_url}/api/brand-studio?action=asset&workspace_id={mos_workspace_id}"
            body = json.dumps({"ref": ref, "purpose": purpose, "max_bytes": max_bytes},
                              sort_keys=True, separators=(",", ":")).encode()
            headers = self._signed_headers(method="POST", url=url, body=body, act="brand.asset", user_id=user_id,
                                           mos_workspace_id=mos_workspace_id)
            req = urllib.request.Request(url, method="POST", data=body, headers=headers)
        else:
            url = ref.get("source_url") or f"{self.base_url}/api/assets/{ref.get('asset_id') or ref.get('asset_version_id')}"
            req = urllib.request.Request(url, method="GET", headers=self._headers())
        try:
            with self._open(req, timeout=30) as resp:
                data = resp.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise GatewayUnavailable(f"asset exceeds maximum allowed bytes ({max_bytes})")
                return data
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                raise GatewayRefused(exc.code, f"Marketing OS refused the asset ({exc.code})") from None
            raise GatewayUnavailable(f"failed to fetch asset: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GatewayUnavailable(f"failed to fetch asset: {exc}") from None

    def campaign_sources(self, *, mos_workspace_id: str, visual_request: dict, user_id: str | None = None) -> dict:
        """POST the visual request: MOS answers ``mos-campaign-sources-v1`` for a placement job and
        ``mos-library-sources-v1`` for an image-library brief (``source: library``). The GET form returns the concept
        browser's listing, which is not an intake source document."""
        url = f"{self.base_url}/api/creative-studio?action=sources&workspace_id={mos_workspace_id}"
        # Canonical (RFC 8785) bytes: Marketing OS's runtime hands its handler a parsed body and re-serialises it to
        # check the signed body hash, which only matches compact, key-sorted, ECMAScript-formatted JSON (403 before).
        body = _jcs.canonical_bytes({"visual_request": visual_request})
        req = urllib.request.Request(url, method="POST", data=body, headers=self._signed_headers(
            method="POST", url=url, body=body, act="campaign.sources", user_id=user_id,
            mos_workspace_id=mos_workspace_id))
        try:
            with self._open(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GatewayUnavailable(f"failed to fetch campaign sources: {exc}") from None

    def eligibility(self, *, mos_workspace_id: str, user_id: str, action: str, source_versions: list) -> dict:
        url = f"{self.base_url}/api/creative-studio?action=eligibility&workspace_id={mos_workspace_id}"
        body = _jcs.canonical_bytes({"action": action, "source_versions": source_versions})
        req = urllib.request.Request(url, method="POST", data=body, headers=self._signed_headers(
            method="POST", url=url, body=body, act="generation.eligibility", user_id=user_id,
            mos_workspace_id=mos_workspace_id))
        try:
            with self._open(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GatewayUnavailable(f"failed to check eligibility: {exc}") from None

    def deliver_handoff(self, *, mos_workspace_id: str, handoff: dict, user_id: str | None = None,
                        assertion_key: str | None = None, clock=time.time) -> dict:
        """POST the immutable handoff to MOS. With `assertion_key` + `user_id` the request carries the signed service
        assertion (ENG-15 format, audience `marketing-os`, subject = the run's requester, action `handoff.deliver`,
        binding = the MOS workspace, bsha = sha256(raw body), exp-iat <= 60 s, random nonce). MOS verifies it; CS never
        treats a response as delivery unless it carries a verifiable ingestion receipt."""
        url = f"{self.base_url}/api/creative-studio?action=ingest&workspace_id={mos_workspace_id}"
        body = json.dumps({"handoff": handoff}, sort_keys=True, separators=(",", ":")).encode()
        saved, self._clock = self._clock, clock
        try:
            headers = self._signed_headers(method="POST", url=url, body=body, act="handoff.deliver", user_id=user_id,
                                           mos_workspace_id=mos_workspace_id, key=assertion_key)
        finally:
            self._clock = saved
        req = urllib.request.Request(url, method="POST", data=body, headers=headers)
        try:
            with self._open(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                raise GatewayRefused(exc.code, f"Marketing OS refused the handoff ({exc.code})") from None
            raise GatewayUnavailable(f"failed to deliver handoff: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GatewayUnavailable(f"failed to deliver handoff: {exc}") from None
