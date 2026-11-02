"""Higgsfield account login (OAuth) as the workspace media credential.

The Higgsfield CLI only accepts its own OAuth credentials file, never an API key. Marketing OS runs the sign-in
(Clerk at clerk.higgsfield.ai, public client + PKCE) and hands Creative Studio the resulting login in the CLI's
file format. Creative Studio is then the ONLY holder that refreshes it:

  * refresh tokens rotate on every use, and presenting an already-used one revokes the whole login (Clerk reuse
    detection, proven 2026-11-02) - so a refresh must be single-writer (the caller holds the credential row lock)
    and the rotated token must be persisted before anything else uses the login;
  * the CLI never refreshes it: we refresh well before expiry, so the CLI always sees a fresh access token.

Nothing here logs or returns token values.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

from .adapter import engine_higgsfield as hf
from .credentials import materialise

TOKEN_URL = "https://clerk.higgsfield.ai/oauth/token"
CLIENT_ID_ENV = "CS_HIGGSFIELD_OAUTH_CLIENT_ID"
# Cloudflare in front of Clerk refuses Python's default user agent (403, error 1010).
USER_AGENT = "MarketingOS-CreativeStudio/1.0"
AUTH_VERSION = 2                       # the CLI rejects older files ("older auth flow")
REFRESH_MARGIN_SECONDS = 3600          # refresh when less than this remains (tokens live 24h)
REQUIRED_KEYS = ("auth_version", "access_token", "refresh_token", "expires_at", "token_type", "scope")
# The Higgsfield workspace this login bills. The CLI refuses to work without one ("No workspace selected") and
# ignores this extra field in its credentials file; Creative Studio also passes it as HIGGSFIELD_WORKSPACE_ID.
OPTIONAL_KEYS = ("workspace_id",)


class LoginError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def parse_login(raw: str | bytes) -> dict:
    """Validate a login in the CLI's credentials-file shape. Raises LoginError('invalid_login')."""
    try:
        login = json.loads(raw)
    except (TypeError, ValueError):
        raise LoginError("invalid_login", "sign in to Higgsfield; API keys are not supported") from None
    if not isinstance(login, dict) or set(login) - set(REQUIRED_KEYS) - set(OPTIONAL_KEYS) \
            or any(k not in login for k in REQUIRED_KEYS):
        raise LoginError("invalid_login", "the login does not have the Higgsfield CLI credential fields")
    if login["auth_version"] != AUTH_VERSION:
        raise LoginError("invalid_login", "unsupported auth_version")
    for key in ("access_token", "refresh_token", "token_type", "scope"):
        if not isinstance(login[key], str) or not login[key].strip() or len(login[key]) > 4096:
            raise LoginError("invalid_login", f"{key} is missing")
    if not isinstance(login["expires_at"], int) or isinstance(login["expires_at"], bool):
        raise LoginError("invalid_login", "expires_at must be an integer epoch")
    if "offline_access" not in login["scope"].split():
        raise LoginError("invalid_login", "the login cannot be refreshed (offline_access missing)")
    if "workspace_id" in login and not (isinstance(login["workspace_id"], str) and 0 < len(login["workspace_id"]) <= 64):
        raise LoginError("invalid_login", "workspace_id is invalid")
    return login


def login_from_token_response(tok: dict, now: Optional[int] = None) -> dict:
    """Build the CLI credentials-file shape from an OAuth token response."""
    now = int(time.time()) if now is None else now
    try:
        expires_in = int(tok["expires_in"])
        login = {"auth_version": AUTH_VERSION, "access_token": tok["access_token"], "refresh_token": tok["refresh_token"],
                 "expires_at": now + expires_in, "token_type": str(tok.get("token_type") or "bearer").lower(),
                 "scope": tok["scope"]}
    except (KeyError, TypeError, ValueError):
        raise LoginError("refresh_failed", "token response is missing fields") from None
    return parse_login(json.dumps(login))


def needs_refresh(login: dict, now: Optional[int] = None, margin: int = REFRESH_MARGIN_SECONDS) -> bool:
    now = int(time.time()) if now is None else now
    return login["expires_at"] - now < margin


def _post_form(url: str, form: dict, timeout: float = 20.0) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT,
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b"{}")
        except ValueError:
            body = {}
        return exc.code, body if isinstance(body, dict) else {}


def refresh(login: dict, *, client_id: Optional[str] = None,
            post: Callable[[str, dict], tuple[int, dict]] = _post_form, now: Optional[int] = None) -> dict:
    """Exchange the refresh token for a new login. The caller MUST persist the result before using it again.

    LoginError('reconnect_required') = Higgsfield refused the refresh token (revoked/expired/reused): only a new
    sign-in fixes it. LoginError('refresh_unavailable') = transient; the stored login is still the current one.
    """
    client_id = client_id or os.environ.get(CLIENT_ID_ENV, "")
    if not client_id:
        raise LoginError("refresh_unavailable", f"{CLIENT_ID_ENV} is not configured")
    try:
        status, body = post(TOKEN_URL, {"grant_type": "refresh_token", "refresh_token": login["refresh_token"],
                                        "client_id": client_id})
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LoginError("refresh_unavailable", type(exc).__name__) from None
    if status == 200:
        return login_from_token_response(body, now)
    if status in (400, 401) and body.get("error") in ("invalid_grant", "invalid_client", "unauthorized_client"):
        raise LoginError("reconnect_required", "Higgsfield no longer accepts this sign-in; sign in again")
    raise LoginError("refresh_unavailable", f"token endpoint answered {status}")


def choose_workspace(login_json: bytes, *, list_workspaces: Callable = None) -> str:
    """The Higgsfield workspace to bill: the only one, else the first the user owns, else the first listed."""
    list_workspaces = list_workspaces or hf.workspace_list
    with materialise(login_json) as path:
        credential = hf.WorkspaceCredential(workspace_id="verification", credential_id="verification",
                                            credentials_path=path)
        try:
            items = list_workspaces(credential)
        except hf.ProviderRefusal as exc:
            raise LoginError(exc.code, exc.detail) from None
        except RuntimeError:
            raise LoginError("login_rejected", "Higgsfield did not accept this sign-in") from None
    usable = [w for w in items if isinstance(w, dict) and isinstance(w.get("id"), str) and w["id"]]
    if not usable:
        raise LoginError("no_higgsfield_workspace", "this Higgsfield account has no workspace to bill")
    owned = [w for w in usable if w.get("user_role") == "owner"]
    return (usable if len(usable) == 1 else owned or usable)[0]["id"]


def account_status(login_json: bytes, *, status: Callable = None, workspace_id: Optional[str] = None) -> dict:
    """Free check through the adapter (`account status --json`) with this login. Returns {credits, email}."""
    status = status or hf.account_status
    with materialise(login_json) as path:
        credential = hf.WorkspaceCredential(workspace_id="verification", credential_id="verification",
                                            credentials_path=path, provider_workspace_id=workspace_id)
        try:
            data = status(credential)
        except hf.ProviderRefusal as exc:
            raise LoginError(exc.code, exc.detail) from None
        except RuntimeError:
            raise LoginError("login_rejected", "Higgsfield did not accept this sign-in") from None
    try:
        credits, email = data["credits"], data["email"]
    except (KeyError, TypeError):
        raise LoginError("verification_unavailable", "the balance check returned no account") from None
    if not isinstance(email, str) or "@" not in email or not isinstance(credits, (int, float)) or isinstance(credits, bool):
        raise LoginError("verification_unavailable", "the balance check returned no account")
    return {"credits": credits, "email": email}


def account_fingerprint(email: str) -> str:
    """Stable per Higgsfield account (not per token), so a refreshed login keeps its sealed binding."""
    return "hf-acct-" + hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]


def masked_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return (local[:2] + "***@" + domain)[:120]
