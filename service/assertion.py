"""MOS -> Creative Studio signed service assertion (ENG-15 Part CS).

Header ``X-CS-Service-Assertion: <b64url(payload-json)>.<b64url(HMAC-SHA256(key, payload-json))>``
payload = {aud, sub, act, bnd, bsha, nonce, iat, exp}. It rides ALONGSIDE the user's bearer token; it never
replaces it. Verification order: bearer (caller) -> signature -> shape -> aud -> sub == bearer user -> act ==
route action -> bsha == sha256(raw body) -> iat/exp window -> binding active for the URL workspace -> nonce
consumed exactly once (insert-only table, unique nonce). Refusals are typed and echo no key or payload.

Routes are declared in POLICY by handler name. ``required`` routes refuse a missing header; the others (reads and
routes the native Studio UI also uses) are native when no header is sent, and fully verified when one is.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import uuid
import time
from dataclasses import dataclass

from .errors import ServiceError

HEADER_ENV_KEY = "HTTP_X_CS_SERVICE_ASSERTION"
AUDIENCE = "creative-studio"
MAX_LIFETIME_SECONDS = 60
MIN_KEY_BYTES = 32
MIN_NONCE_BYTES = 16
_FIELDS = {"aud", "sub", "act", "bnd", "bsha", "nonce", "iat", "exp"}
# Optional route binding Marketing OS signs: "<METHOD> <path>[?sorted query]". When present it must match this request.
_OPTIONAL = {"pth"}


@dataclass(frozen=True)
class Policy:
    action: str
    required: bool = True
    binds: bool = False      # connection creation: no binding exists yet; bnd must equal body.external_workspace_id
    resolves: bool = False   # binding lookup: the URL names the Marketing OS workspace; bnd must equal it


POLICY: dict[str, Policy] = {
    # connections / bindings
    "b2_create_connection": Policy("connection.bind", binds=True),
    "b2_list_connections": Policy("connection.list", required=False),
    "b2_refresh_connection": Policy("connection.refresh"),
    "cs21_resolve_binding": Policy("connection.resolve", resolves=True),
    # brand package import
    "b2_import_brand_package": Policy("brand_package.import"),
    "b2_get_operation": Policy("operation.read", required=False),
    "b2_get_brand_package": Policy("brand_package.read", required=False),
    # creative request intake, treatments, compile
    "b2_intake_marketing_os": Policy("request.intake"),
    "b2_create_request": Policy("request.create"),
    "b2_revise_request": Policy("request.revise"),
    "b2_get_request": Policy("request.read", required=False),
    "b2_create_treatment": Policy("treatment.create"),
    "b2_revise_treatment": Policy("treatment.revise"),
    "b2_accept_treatment": Policy("treatment.accept"),
    "b2_get_treatment": Policy("treatment.read", required=False),
    "b2_compile_mapping": Policy("compile.map"),
    "cs16_compile_plan": Policy("plan.compile", required=False),
    "cs16_get_plan": Policy("plan.read", required=False),
    "cs_b3_quote": Policy("quote.create"),
    # run admission
    "cs_b3_admit_run": Policy("run.admit"),
    "cs_b3_cancel_run": Policy("run.cancel"),
    "cs_b3_run_costs": Policy("run.costs", required=False),
    # export / handoff (the native Studio UI also calls handoff create/read)
    "cs_b4_create_handoff": Policy("handoff.create", required=False),
    "cs_b4_get_handoff": Policy("handoff.read", required=False),
    "cs_b4_get_export": Policy("export.read", required=False),
    # CS-17b: MOS pulls the exported file / reports its ingestion receipt
    "cs17b_export_download_ticket": Policy("export.download", required=False),
    "cs17b_record_handoff_receipt": Policy("handoff.receipt"),
    # CS-19: BYOK credential forwarded once at connect/rotation; funding policy. `funding.platform` is signed by
    # MOS only for an operator-authorised workspace.
    "cs19_store_credential": Policy("credential.store"),
    "cs19_revoke_credential": Policy("credential.revoke"),
    "cs19_funding_state": Policy("funding.read"),
    "cs19_funding_byok": Policy("funding.byok"),
    "cs19_funding_platform": Policy("funding.platform"),
}


def _refuse(code: str, status: int = 401) -> ServiceError:
    return ServiceError(status, code)


def _b64d(text: str) -> bytes:
    if not text or len(text) > 4096:
        raise _refuse("assertion_invalid")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise _refuse("assertion_invalid") from None


def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def sign(key: str, payload: dict) -> str:
    """Reference signer (tests, and the contract MOS implements)."""
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return f"{b64e(raw)}.{b64e(hmac.new(key.encode(), raw, hashlib.sha256).digest())}"


def verify(header: str, *, key: str, user_id: str, action: str, raw_body: bytes, now: float, skew: int,
           target: str | None = None) -> dict:
    """Pure checks (everything except binding and nonce). Returns the payload."""
    if not key or len(key.encode()) < MIN_KEY_BYTES:
        raise ServiceError(503, "assertion_unconfigured")
    parts = header.split(".")
    if len(parts) != 2:
        raise _refuse("assertion_invalid")
    raw, sig = _b64d(parts[0]), _b64d(parts[1])
    expect = hmac.new(key.encode(), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expect):
        raise _refuse("assertion_invalid")
    try:
        p = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _refuse("assertion_invalid") from None
    if not isinstance(p, dict) or set(p) - _OPTIONAL != _FIELDS:
        raise _refuse("assertion_invalid")
    if "pth" in p and (not isinstance(p["pth"], str) or target is None
                      or not hmac.compare_digest(p["pth"].encode(), target.encode())):
        raise _refuse("assertion_invalid")
    for name in ("aud", "sub", "act", "bnd", "bsha", "nonce"):
        if not isinstance(p[name], str):
            raise _refuse("assertion_invalid")
    for name in ("iat", "exp"):
        if isinstance(p[name], bool) or not isinstance(p[name], int):
            raise _refuse("assertion_invalid")
    if p["aud"] != AUDIENCE:
        raise _refuse("assertion_invalid")
    if not hmac.compare_digest(p["sub"].lower().encode(), user_id.lower().encode()):
        raise _refuse("assertion_invalid")
    if p["act"] != action:
        raise _refuse("assertion_invalid")
    if not hmac.compare_digest(p["bsha"].lower().encode(), hashlib.sha256(raw_body).hexdigest().encode()):
        raise _refuse("assertion_invalid")
    if not 0 < p["exp"] - p["iat"] <= MAX_LIFETIME_SECONDS:
        raise _refuse("assertion_invalid")
    try:
        nonce_bytes = _b64d(p["nonce"])
    except ServiceError:
        raise _refuse("assertion_invalid") from None
    if len(nonce_bytes) < MIN_NONCE_BYTES or len(p["nonce"]) > 128:
        raise _refuse("assertion_invalid")
    if now > p["exp"] + skew:
        raise _refuse("assertion_expired")
    if p["iat"] > now + skew:
        raise _refuse("assertion_invalid")
    return p


# Marketing OS server-to-server callbacks (no user session exists when Creative Studio delivers a handoff from its
# worker): for these immutable reads and the receipt only, a valid route-bound service assertion IS the authentication.
SERVICE_ONLY_ACTIONS = frozenset({"handoff.read", "export.read", "export.download", "handoff.receipt"})


def claimed_subject(header: str) -> str | None:
    """The `sub` an assertion claims, read BEFORE verification only to pick the user to verify it against; `verify`
    then checks the signature over that exact payload, so a forged sub cannot pass."""
    try:
        p = json.loads(_b64d(header.split(".")[0]).decode())
        return str(uuid.UUID(str(p["sub"])))
    except Exception:
        return None


def request_target(req) -> str:
    """The route an assertion may be bound to: method + path (+ query sorted by key, then value)."""
    from urllib.parse import parse_qsl, urlencode
    query = parse_qsl(req._environ.get("QUERY_STRING", ""), keep_blank_values=True)
    # Vercel's `/api/v1/:path*` rewrite appends `path=<the matched suffix>`; that is platform routing, not the request.
    suffix = req.path[len("/api/v1/"):] if req.path.startswith("/api/v1/") else None
    query = sorted((k, v) for k, v in query if not (k == "path" and v == suffix))
    return f"{req.method} {req.path}" + (f"?{urlencode(query)}" if query else "")


class AssertionGuard:
    def __init__(self, settings, connect, clock=time.time):
        self.settings = settings
        self._connect = connect
        self.clock = clock

    def check(self, req, policy: Policy, *, actor, workspace_id: str) -> dict | None:
        header = req._environ.get(HEADER_ENV_KEY)
        if not header:
            if policy.required:
                raise _refuse("assertion_missing")
            return None
        payload = verify(header.strip(), key=self.settings.mos_assertion_key or "", user_id=actor.user_id,
                         action=policy.action, raw_body=req.raw_body(), now=self.clock(),
                         skew=self.settings.assertion_skew_seconds, target=request_target(req))
        if policy.binds:
            if payload["bnd"] != str(req.json().get("external_workspace_id")):
                raise _refuse("binding_inactive", 403)
        if policy.resolves and payload["bnd"] != workspace_id:
            raise _refuse("binding_inactive", 403)
        expires = payload["exp"] + self.settings.assertion_skew_seconds
        conn = self._connect(self.settings.api_database_url)
        try:
            with conn:
                cur = conn.cursor()
                if not policy.binds and not policy.resolves:
                    cur.execute("select public.cs_assertion_binding_ok(%s::uuid, %s)", (workspace_id, payload["bnd"]))
                    if not cur.fetchone()[0]:
                        raise _refuse("binding_inactive", 403)
                cur.execute("select public.cs_consume_assertion_nonce(%s, to_timestamp(%s))", (payload["nonce"], expires))
                if not cur.fetchone()[0]:
                    raise _refuse("assertion_replayed")
        finally:
            conn.close()
        return payload
