"""Private storage access.

The API never returns a storage key, bucket path, provider URL or service
credential. A download is: (1) cs_create_download_grant (<=60 s exact-object
grant, after current authorization in SQL), then (2) Supabase Storage mints a
signed URL *as the user*; Storage's own RLS calls cs_storage_allowed, so the
URL can only be minted while the grant is live. The URL itself expires in
<=300 s. Its origin is checked to be the configured Storage host.
"""
from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urljoin, urlparse

from .config import SIGNED_DOWNLOAD_MAX_SECONDS, ConfigError, Settings
from .errors import ServiceError

BUCKET = "creative-studio"


class StorageSigner:
    def __init__(self, settings: Settings, opener=None):
        self.settings = settings
        self._open = opener or urllib.request.urlopen

    def sign_download(self, user_token: str, storage_key: str, seconds: int) -> str:
        if not 1 <= seconds <= SIGNED_DOWNLOAD_MAX_SECONDS:
            raise ServiceError(422, "signed_url_ttl_out_of_bounds")
        endpoint = f"{self.settings.storage_url}/object/sign/{BUCKET}/{quote(storage_key, safe='/')}"
        req = urllib.request.Request(
            endpoint, method="POST", data=json.dumps({"expiresIn": seconds}).encode(),
            headers={"Authorization": f"Bearer {user_token}", "apikey": self.settings.storage_anon_key,
                     "Content-Type": "application/json"})
        try:
            with self._open(req, timeout=10) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            # Storage answers 400/403/404 when RLS hides the object (grant expired/absent).
            if exc.code in (400, 401, 403, 404):
                raise ServiceError(403, "download_not_permitted") from None
            raise ServiceError(502, "storage_unavailable", f"status {exc.code}") from None
        except urllib.error.URLError:
            raise ServiceError(502, "storage_unavailable") from None
        signed = body.get("signedURL") or body.get("signedUrl")
        if not isinstance(signed, str) or not signed:
            raise ServiceError(502, "storage_unavailable", "no signed url")
        if signed.startswith("//") or "\\" in signed:
            raise ServiceError(502, "storage_unavailable", "unexpected signed url origin")
        url = urljoin(self.settings.storage_url + "/", signed.lstrip("/")) if signed.startswith("/") else signed
        if not url.startswith(self.settings.storage_origin + "/") or urlparse(url).netloc != urlparse(
                self.settings.storage_url).netloc:
            raise ServiceError(502, "storage_unavailable", "unexpected signed url origin")
        return url


REFRESH_MARGIN_SECONDS = 120


class WorkerAuthError(ServiceError):
    """Typed worker-identity failure. The detail never contains a credential or token."""


def _jwt_exp(token: str) -> float | None:
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(claims["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return None


class WorkerTokenProvider:
    """Signs in the dedicated worker auth user (password grant), caches the access token
    until exp - 120 s, refreshes thread-safely. Callable: provider() -> bearer token."""

    def __init__(self, settings: Settings, opener=None, clock=time.time):
        self.settings = settings
        self._open = opener or urllib.request.urlopen
        self._clock = clock
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    def __call__(self) -> str:
        return self.token()

    def token(self) -> str:
        with self._lock:
            if self._token and self._clock() < self._expires_at - REFRESH_MARGIN_SECONDS:
                return self._token
            return self._sign_in()

    def invalidate(self, stale: str) -> None:
        """Drop the cache only if it still holds the rejected token (others may have refreshed)."""
        with self._lock:
            if self._token == stale:
                self._token = None
                self._expires_at = 0.0

    def _sign_in(self) -> str:
        try:
            email, password = self.settings.require_worker_identity()
        except ConfigError:
            raise WorkerAuthError(503, "worker_identity_unconfigured") from None
        req = urllib.request.Request(
            f"{self.settings.auth_base_url}/token?grant_type=password", method="POST",
            data=json.dumps({"email": email, "password": password}).encode(),
            headers={"apikey": self.settings.storage_anon_key, "Content-Type": "application/json"})
        try:
            with self._open(req, timeout=10) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401, 403, 422):
                raise WorkerAuthError(503, "worker_signin_failed", f"status {exc.code}") from None
            raise WorkerAuthError(502, "worker_auth_unavailable", f"status {exc.code}") from None
        except (urllib.error.URLError, OSError, ValueError):
            raise WorkerAuthError(502, "worker_auth_unavailable") from None
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise WorkerAuthError(502, "worker_auth_unavailable", "no access token in response")
        exp = _jwt_exp(token)
        if exp is None:
            try:
                exp = self._clock() + float(body["expires_in"])
            except (KeyError, TypeError, ValueError):
                raise WorkerAuthError(502, "worker_auth_unavailable", "no token expiry") from None
        self._token, self._expires_at = token, exp
        return token


def _is_auth_rejection(exc: urllib.error.HTTPError) -> bool:
    """Storage reports an expired/invalid bearer as 401, or as 400/403 naming the JWT."""
    if exc.code == 401:
        return True
    if exc.code in (400, 403):
        try:
            return "jwt" in exc.read().decode("utf-8", "replace").lower()
        except Exception:  # unreadable body: not an auth rejection we can prove
            return False
    return False


class StorageObjectStore:
    """Production ObjectStore implementation for worker stages backed by Supabase Storage."""

    def __init__(self, settings: Settings, token_provider: WorkerTokenProvider | None = None, opener=None,
                 auth_opener=None):
        self.settings = settings
        self._open = opener or urllib.request.urlopen
        self.tokens = token_provider or WorkerTokenProvider(settings, opener=auth_opener)

    def _call(self, method: str, url: str, data: bytes | None, headers: dict, timeout: int):
        """One authorised request; on an auth rejection refresh once and retry once."""
        for attempt in (0, 1):
            bearer = self.tokens.token()
            req = urllib.request.Request(
                url, method=method, data=data,
                headers={**headers, "Authorization": f"Bearer {bearer}", "apikey": self.settings.storage_anon_key})
            try:
                with self._open(req, timeout=timeout) as resp:
                    return resp.status, resp.read()
            except urllib.error.HTTPError as exc:
                if attempt == 0 and _is_auth_rejection(exc):
                    self.tokens.invalidate(bearer)
                    continue
                raise
        raise AssertionError("unreachable")

    def put(self, storage_key: str, data: bytes, mime_type: str) -> None:
        url = f"{self.settings.storage_url}/object/{BUCKET}/{quote(storage_key, safe='/')}"
        try:
            status, _ = self._call("POST", url, data, {"Content-Type": mime_type}, 30)
            if status not in (200, 201):
                raise ServiceError(502, "storage_put_failed", f"status {status}")
        except urllib.error.HTTPError as exc:
            raise ServiceError(502, "storage_put_failed", f"status {exc.code}") from None
        except urllib.error.URLError:
            raise ServiceError(502, "storage_unavailable") from None

    def get(self, storage_key: str) -> bytes:
        url = f"{self.settings.storage_url}/object/authenticated/{BUCKET}/{quote(storage_key, safe='/')}"
        try:
            return self._call("GET", url, None, {}, 30)[1]
        except urllib.error.HTTPError as exc:
            raise ServiceError(502, "storage_get_failed", f"status {exc.code}") from None
        except urllib.error.URLError:
            raise ServiceError(502, "storage_unavailable") from None

    def probe(self) -> None:
        """Minimal authorised storage call proving the worker credential is accepted.
        Lists at most one object under the environment prefix (SELECT is admitted for the
        principal only on its own environment prefix). Raises a typed ServiceError."""
        url = f"{self.settings.storage_url}/object/list/{BUCKET}"
        body = json.dumps({"prefix": f"{self.settings.environment_id}/", "limit": 1}).encode()
        try:
            self._call("POST", url, body, {"Content-Type": "application/json"}, 10)
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401, 403):
                raise WorkerAuthError(503, "storage_credential_rejected", f"status {exc.code}") from None
            raise ServiceError(502, "storage_unavailable", f"status {exc.code}") from None
        except urllib.error.URLError:
            raise ServiceError(502, "storage_unavailable") from None


_PROVIDERS: dict[tuple, WorkerTokenProvider] = {}
_PROVIDERS_LOCK = threading.Lock()


def worker_token_provider(settings: Settings) -> WorkerTokenProvider:
    """Process-wide provider so warm serverless invocations reuse the cached access token."""
    key = (settings.auth_base_url, settings.storage_worker_email, settings.storage_worker_password,
           settings.storage_anon_key)
    with _PROVIDERS_LOCK:
        provider = _PROVIDERS.get(key)
        if provider is None:
            provider = _PROVIDERS[key] = WorkerTokenProvider(settings)
        return provider


def make_storage_store(settings: Settings) -> StorageObjectStore:
    settings.require_worker_identity()   # fail closed with a typed ConfigError
    return StorageObjectStore(settings, token_provider=worker_token_provider(settings))
