"""Real HTTP client for Marketing OS gateway integration (CS-B5).

Communicates with Marketing OS over HTTP using bearer service authentication
or per-request credentials. Isolated in service.runtime to preserve CS-B2
hygiene invariants (zero network calls in brand domain code).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from service.brand.gateway import GatewayUnavailable


class HttpMarketingOsGateway:
    """Real HTTP client for the Marketing OS gateway."""

    def __init__(self, base_url: str, service_token: str | None = None, opener=None):
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self._open = opener or urllib.request.urlopen

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.service_token:
            h["Authorization"] = f"Bearer {self.service_token}"
        return h

    def authorize_binding(self, *, user_id: str, mos_workspace_id: str) -> dict:
        url = f"{self.base_url}/api/creative-studio?action=authorize&workspace_id={mos_workspace_id}"
        req = urllib.request.Request(url, method="GET", headers=self._headers())
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

    def export_brand_kit(self, *, mos_workspace_id: str, artifact_id: str) -> dict:
        url = f"{self.base_url}/api/brand-studio?action=export&workspace_id={mos_workspace_id}&artifact_id={artifact_id}"
        req = urllib.request.Request(url, method="GET", headers=self._headers())
        try:
            with self._open(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GatewayUnavailable(f"failed to export brand kit: {exc}") from None

    def fetch_asset(self, *, mos_workspace_id: str, ref: dict, purpose: str, max_bytes: int) -> bytes:
        url = ref.get("source_url") or f"{self.base_url}/api/assets/{ref.get('asset_id') or ref.get('asset_version_id')}"
        req = urllib.request.Request(url, method="GET", headers=self._headers())
        try:
            with self._open(req, timeout=30) as resp:
                data = resp.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise GatewayUnavailable(f"asset exceeds maximum allowed bytes ({max_bytes})")
                return data
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GatewayUnavailable(f"failed to fetch asset: {exc}") from None

    def campaign_sources(self, *, mos_workspace_id: str, visual_request: dict) -> dict:
        concept_id = visual_request.get("concept_id") or visual_request.get("placement_id") or ""
        url = f"{self.base_url}/api/creative-studio?action=sources&workspace_id={mos_workspace_id}&concept_id={concept_id}"
        req = urllib.request.Request(url, method="GET", headers=self._headers())
        try:
            with self._open(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GatewayUnavailable(f"failed to fetch campaign sources: {exc}") from None

    def eligibility(self, *, mos_workspace_id: str, user_id: str, action: str, source_versions: list) -> dict:
        url = f"{self.base_url}/api/creative-studio?action=eligibility&workspace_id={mos_workspace_id}"
        body = json.dumps({"action": action, "source_versions": source_versions}).encode()
        req = urllib.request.Request(url, method="POST", data=body, headers=self._headers())
        try:
            with self._open(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GatewayUnavailable(f"failed to check eligibility: {exc}") from None

    def deliver_handoff(self, *, mos_workspace_id: str, handoff: dict) -> dict:
        url = f"{self.base_url}/api/creative-studio?action=ingest&workspace_id={mos_workspace_id}"
        body = json.dumps({"handoff": handoff}).encode()
        req = urllib.request.Request(url, method="POST", data=body, headers=self._headers())
        try:
            with self._open(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GatewayUnavailable(f"failed to deliver handoff: {exc}") from None
