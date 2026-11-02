"""CS-19 BYOK credential + media-funding routes (all behind the ENG-15 signed gateway assertion).

  PUT    /api/v1/workspaces/{ws}/provider-credentials/higgsfield  store/rotate the workspace's key (sealed, once)
  DELETE /api/v1/workspaces/{ws}/provider-credentials/higgsfield  revoke it
  GET    /api/v1/workspaces/{ws}/funding                          readable state (no key material, ever)
  PUT    /api/v1/workspaces/{ws}/funding/byok-only                workspace key only (platform credential never used)
  PUT    /api/v1/workspaces/{ws}/funding/platform                 platform credential allowed (MOS signs this only for
                                                                  an operator-authorised workspace)

The credential (a Higgsfield account login, verified with a free balance check) arrives once in the request body, is sealed with AES-256-GCM (CS_CREDENTIAL_KEYS) and stored through
cs_store_workspace_credential. It is never logged, never echoed, never part of an idempotency digest, and never
returned. Rotation = a new PUT (the previous version is revoked in the same transaction).
"""
from __future__ import annotations

import re

from ..errors import ServiceError
from ..providers.credentials import CredentialError, KeyRing
from ..providers import higgsfield_oauth as hf_login

_UUID = r"[0-9a-fA-F-]{36}"
PROVIDER = "higgsfield"
_MAX_CREDENTIAL_CHARS = 16384
_MIN_CREDENTIAL_CHARS = 8
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _uuid(value: str) -> str:
    import uuid
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError):
        raise ServiceError(404, "resource_unavailable") from None


def _credential_from(body: dict) -> str:
    if set(body) - {"credential"}:
        raise ServiceError(422, "invalid_credential", "only the credential field is accepted")
    value = body.get("credential")
    if not isinstance(value, str) or not value.strip():
        raise ServiceError(422, "invalid_credential", "credential is required")
    if not (_MIN_CREDENTIAL_CHARS <= len(value) <= _MAX_CREDENTIAL_CHARS) or _CONTROL.search(value):
        raise ServiceError(422, "invalid_credential", "credential has an unsupported shape")
    return value


def _keyring(api) -> KeyRing:
    injected = api.__dict__.get("credential_keyring")
    if injected is not None:
        return injected
    try:
        return KeyRing.from_env()
    except CredentialError:
        raise ServiceError(503, "credential_store_unavailable") from None


def register(api_cls, route) -> None:
    @route("PUT", rf"/api/v1/workspaces/({_UUID})/provider-credentials/{PROVIDER}")
    def cs19_store_credential(self, req, workspace_id):
        workspace_id = _uuid(workspace_id)
        credential = _credential_from(req.json())
        # The credential is a Higgsfield account login (CLI credentials-file shape) from Marketing OS's sign-in.
        # Verify it with the free balance check before storing: "connected" must mean Higgsfield accepted it.
        try:
            hf_login.parse_login(credential)
            account = hf_login.account_status(credential.encode())
        except hf_login.LoginError as exc:
            status = 422 if exc.code in ("invalid_login", "login_rejected") else 503
            raise ServiceError(status, exc.code, exc.detail) from None
        fp = hf_login.account_fingerprint(account["email"])
        sealed = _keyring(self).seal(credential.encode(), provider=PROVIDER, fingerprint=fp)
        del credential
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select public.cs_store_workspace_credential(%s, %s, %s, %s, %s, %s, %s) as version_id",
                        (workspace_id, self.settings.environment_id, PROVIDER, fp, sealed.ciphertext, sealed.nonce,
                         sealed.key_version))
            version_id = cur.fetchone()["version_id"]
        return 200, {"stored": True, "provider": PROVIDER, "version_id": str(version_id), "fingerprint_tail": fp[-4:],
                     "account": hf_login.masked_email(account["email"]), "credits": account["credits"]}

    @route("DELETE", rf"/api/v1/workspaces/({_UUID})/provider-credentials/{PROVIDER}")
    def cs19_revoke_credential(self, req, workspace_id):
        workspace_id = _uuid(workspace_id)
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select public.cs_revoke_workspace_credential(%s, %s) as n", (workspace_id, PROVIDER))
            n = cur.fetchone()["n"]
        return 200, {"revoked": n > 0, "provider": PROVIDER}

    @route("GET", rf"/api/v1/workspaces/({_UUID})/funding")
    def cs19_funding_state(self, req, workspace_id):
        workspace_id = _uuid(workspace_id)
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select * from public.cs_workspace_funding_state(%s, %s)", (workspace_id, PROVIDER))
            row = cur.fetchone()
        return 200, {"media_funding": row["media_funding"], "provider": PROVIDER,
                     "workspace_credential": row["workspace_credential"],
                     "fingerprint_tail": row["fingerprint_tail"],
                     "credential_since": row["credential_since"],
                     "platform_credential_usable": row["platform_credential_usable"]}

    def _set_funding(self, req, workspace_id, mode):
        workspace_id = _uuid(workspace_id)
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select public.cs_set_workspace_funding(%s, %s) as mode", (workspace_id, mode))
            return 200, {"media_funding": cur.fetchone()["mode"]}

    @route("PUT", rf"/api/v1/workspaces/({_UUID})/funding/byok-only")
    def cs19_funding_byok(self, req, workspace_id):
        return _set_funding(self, req, workspace_id, "byok_only")

    @route("PUT", rf"/api/v1/workspaces/({_UUID})/funding/platform")
    def cs19_funding_platform(self, req, workspace_id):
        return _set_funding(self, req, workspace_id, "platform_funded")

    for name, fn in list(locals().items()):
        if name.startswith("cs19_"):
            setattr(api_cls, name, fn)
