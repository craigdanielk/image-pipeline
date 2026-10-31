"""Private storage access.

The API never returns a storage key, bucket path, provider URL or service
credential. A download is: (1) cs_create_download_grant (<=60 s exact-object
grant, after current authorization in SQL), then (2) Supabase Storage mints a
signed URL *as the user*; Storage's own RLS calls cs_storage_allowed, so the
URL can only be minted while the grant is live. The URL itself expires in
<=300 s. Its origin is checked to be the configured Storage host.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import quote, urljoin, urlparse

from .config import SIGNED_DOWNLOAD_MAX_SECONDS, Settings
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


class StorageObjectStore:
    """Production ObjectStore implementation for worker stages backed by Supabase Storage."""

    def __init__(self, settings: Settings, token: str | None = None, opener=None):
        self.settings = settings
        self.token = token or settings.storage_worker_credential or ""
        self._open = opener or urllib.request.urlopen

    def put(self, storage_key: str, data: bytes, mime_type: str) -> None:
        url = f"{self.settings.storage_url}/object/{BUCKET}/{quote(storage_key, safe='/')}"
        req = urllib.request.Request(
            url, method="POST", data=data,
            headers={"Authorization": f"Bearer {self.token}", "apikey": self.settings.storage_anon_key,
                     "Content-Type": mime_type})
        try:
            with self._open(req, timeout=30) as resp:
                if resp.status not in (200, 201):
                    raise ServiceError(502, "storage_put_failed", f"status {resp.status}")
        except urllib.error.HTTPError as exc:
            raise ServiceError(502, "storage_put_failed", f"status {exc.code}") from None
        except urllib.error.URLError as exc:
            raise ServiceError(502, "storage_unavailable", str(exc)) from None

    def get(self, storage_key: str) -> bytes:
        url = f"{self.settings.storage_url}/object/authenticated/{BUCKET}/{quote(storage_key, safe='/')}"
        req = urllib.request.Request(
            url, method="GET",
            headers={"Authorization": f"Bearer {self.token}", "apikey": self.settings.storage_anon_key})
        try:
            with self._open(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise ServiceError(502, "storage_get_failed", f"status {exc.code}") from None
        except urllib.error.URLError as exc:
            raise ServiceError(502, "storage_unavailable", str(exc)) from None


def make_storage_store(settings: Settings) -> StorageObjectStore:
    return StorageObjectStore(settings)
