"""Runtime configuration from environment variables only (no files, no defaults
for secrets). Missing required settings refuse service start-up."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

SIGNED_DOWNLOAD_MAX_SECONDS = 300       # database-contract.json storage.signed_download_seconds
DOWNLOAD_GRANT_MAX_SECONDS = 60         # database-contract.json storage.download_grant_seconds


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    api_database_url: str               # cs_api login (transaction pooler, TLS) - provisioned in CS-B5
    worker_database_url: str | None     # cs_worker login; only the worker entrypoint needs it
    environment_id: str
    release_id: str
    jwt_issuer: str
    jwt_audience: str
    jwt_secret: str | None              # HS256 project secret, or
    jwks_url: str | None                # asymmetric signing keys
    storage_url: str                    # https://<project>.supabase.co/storage/v1
    storage_anon_key: str               # public anon key Storage requires alongside the user token
    signed_download_seconds: int
    download_grant_seconds: int
    worker_secret: str | None
    runtime_principal: str

    @property
    def storage_origin(self) -> str:
        p = urlparse(self.storage_url)
        return f"{p.scheme}://{p.netloc}"


def _req(env, name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"missing required setting {name}")
    return value


def _bounded_int(env, name: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(name, "").strip()
    value = int(raw) if raw else default
    if not lo <= value <= hi:
        raise ConfigError(f"{name}={value} outside [{lo}, {hi}]")
    return value


def load(env=None) -> Settings:
    env = os.environ if env is None else env
    environment_id = _req(env, "CS_ENVIRONMENT_ID")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", environment_id):
        raise ConfigError("CS_ENVIRONMENT_ID is not a valid environment id")
    jwt_secret = env.get("CS_JWT_SECRET", "").strip() or None
    jwks_url = env.get("CS_JWKS_URL", "").strip() or None
    if not jwt_secret and not jwks_url:
        raise ConfigError("one of CS_JWT_SECRET or CS_JWKS_URL is required")
    storage_url = _req(env, "CS_STORAGE_URL").rstrip("/")
    if urlparse(storage_url).scheme not in ("https", "http"):
        raise ConfigError("CS_STORAGE_URL must be an http(s) URL")
    return Settings(
        api_database_url=_req(env, "CS_API_DATABASE_URL"),
        worker_database_url=env.get("CS_WORKER_DATABASE_URL", "").strip() or None,
        environment_id=environment_id,
        release_id=_req(env, "CS_RELEASE_ID"),
        jwt_issuer=_req(env, "CS_JWT_ISSUER"),
        jwt_audience=env.get("CS_JWT_AUDIENCE", "authenticated").strip() or "authenticated",
        jwt_secret=jwt_secret,
        jwks_url=jwks_url,
        storage_url=storage_url,
        storage_anon_key=_req(env, "CS_STORAGE_ANON_KEY"),
        signed_download_seconds=_bounded_int(env, "CS_SIGNED_DOWNLOAD_SECONDS", SIGNED_DOWNLOAD_MAX_SECONDS,
                                             1, SIGNED_DOWNLOAD_MAX_SECONDS),
        download_grant_seconds=_bounded_int(env, "CS_DOWNLOAD_GRANT_SECONDS", DOWNLOAD_GRANT_MAX_SECONDS,
                                            1, DOWNLOAD_GRANT_MAX_SECONDS),
        worker_secret=env.get("CS_WORKER_SECRET", "").strip() or None,
        runtime_principal=env.get("CS_RUNTIME_PRINCIPAL", "").strip() or f"cs-worker:{environment_id}",
    )
