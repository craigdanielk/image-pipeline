"""Runtime configuration from environment variables only (no files, no defaults
for secrets). Missing required settings refuse service start-up."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

SIGNED_DOWNLOAD_MAX_SECONDS = 300       # database-contract.json storage.signed_download_seconds
DOWNLOAD_GRANT_MAX_SECONDS = 60         # database-contract.json storage.download_grant_seconds


class ConfigError(RuntimeError):
    pass


class WorkerIdentityUnconfigured(ConfigError):
    """Hosted mode requires the dedicated storage worker auth identity (email + password)."""
    code = "worker_identity_unconfigured"


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
    storage_worker_email: str | None = None      # dedicated worker auth user (password grant)
    storage_worker_password: str | None = field(default=None, repr=False)
    auth_url: str | None = None                  # https://<project>.supabase.co/auth/v1
    storage_worker_id: str | None = None
    mos_gateway_url: str | None = None
    mos_service_token: str | None = None
    mos_assertion_key: str | None = field(default=None, repr=False)   # CS_MOS_ASSERTION_KEY (>= 32 bytes)
    assertion_skew_seconds: int = 30

    @property
    def auth_base_url(self) -> str:
        """Auth base: CS_AUTH_URL, else the JWT issuer (which is the auth base on Supabase)."""
        return (self.auth_url or self.jwt_issuer).rstrip("/")

    def require_worker_identity(self) -> tuple[str, str]:
        if not self.storage_worker_email or not self.storage_worker_password:
            raise WorkerIdentityUnconfigured(
                "CS_STORAGE_WORKER_EMAIL and CS_STORAGE_WORKER_PASSWORD are required")
        return self.storage_worker_email, self.storage_worker_password

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
    worker_email = env.get("CS_STORAGE_WORKER_EMAIL", "").strip() or None
    worker_password = env.get("CS_STORAGE_WORKER_PASSWORD", "") or None
    hosted = bool(env.get("VERCEL", "").strip()) or env.get("CS_HOSTED", "").strip().lower() in ("1", "true", "yes")
    if hosted and not (worker_email and worker_password):
        raise WorkerIdentityUnconfigured("CS_STORAGE_WORKER_EMAIL and CS_STORAGE_WORKER_PASSWORD are required")
    assertion_key = env.get("CS_MOS_ASSERTION_KEY", "").strip() or None
    if assertion_key and len(assertion_key.encode()) < 32:
        raise ConfigError("CS_MOS_ASSERTION_KEY must be at least 32 bytes")
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
        storage_worker_email=worker_email,
        storage_worker_password=worker_password,
        auth_url=(env.get("CS_AUTH_URL", "").strip().rstrip("/") or None),
        storage_worker_id=env.get("CS_STORAGE_WORKER_ID", "").strip() or None,
        mos_gateway_url=env.get("CS_MOS_GATEWAY_URL", "").strip() or env.get("MOS_GATEWAY_URL", "").strip() or None,
        mos_service_token=env.get("CS_MOS_SERVICE_TOKEN", "").strip() or env.get("MOS_SERVICE_TOKEN", "").strip() or None,
        mos_assertion_key=assertion_key,
        assertion_skew_seconds=_bounded_int(env, "CS_ASSERTION_SKEW_SECONDS", 30, 0, 300),
    )
