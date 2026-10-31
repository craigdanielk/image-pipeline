"""Creative Studio hosted API (CS-B1 surface), plain WSGI.

Every domain route: verify bearer token -> one cs_api transaction with the
verified actor -> cs_* procedure or RLS-scoped read. Workspace scope is checked
in SQL on every request from current membership rows; nothing is trusted from
the client except the bearer token. There are no filesystem, jobs_root,
tenant-slug or media-path routes, and unknown query parameters are refused.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime
from decimal import Decimal
from urllib.parse import parse_qs

from . import assertion as _assertion
from .auth import TokenVerifier
from .config import Settings
from .db import actor_transaction
from .errors import ServiceError
from .storage import StorageSigner

log = logging.getLogger("creative_studio.api")

_UUID = r"[0-9a-fA-F-]{36}"
_MAX_BODY = 64 * 1024
_ALLOWED_QUERY = {"after", "limit"}

_ROUTES: list[tuple[str, re.Pattern, str]] = []


def route(method: str, pattern: str):
    def deco(fn):
        _ROUTES.append((method, re.compile("^" + pattern + "$"), fn.__name__))
        return fn
    return deco


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"not serialisable: {type(value).__name__}")


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError):
        raise ServiceError(404, "resource_unavailable") from None


class Request:
    def __init__(self, environ):
        self.method = environ.get("REQUEST_METHOD", "GET").upper()
        self.path = environ.get("PATH_INFO", "") or "/"
        self.query = {k: v[-1] for k, v in parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True).items()}
        self.authorization = environ.get("HTTP_AUTHORIZATION")
        self.idempotency_key = environ.get("HTTP_IDEMPOTENCY_KEY")
        self._environ = environ
        self._body = None
        self._raw = None

    def raw_body(self) -> bytes:
        if self._raw is None:
            try:
                length = int(self._environ.get("CONTENT_LENGTH") or 0)
            except ValueError:
                raise ServiceError(400, "invalid_body") from None
            if length > _MAX_BODY:
                raise ServiceError(413, "body_too_large")
            self._raw = self._environ["wsgi.input"].read(length) if length else b""
        return self._raw

    def json(self) -> dict:
        if self._body is None:
            raw = self.raw_body()
            try:
                self._body = json.loads(raw.decode() or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ServiceError(400, "invalid_body") from None
            if not isinstance(self._body, dict):
                raise ServiceError(400, "invalid_body")
        return self._body

    def require_idempotency(self) -> tuple[str, str]:
        try:
            key = str(uuid.UUID(self.idempotency_key or ""))
        except ValueError:
            raise ServiceError(422, "idempotency_key_required") from None
        canonical = json.dumps({"method": self.method, "path": self.path, "body": self.json()},
                               sort_keys=True, separators=(",", ":"))
        return key, hashlib.sha256(canonical.encode()).hexdigest()

    def bearer_token(self) -> str:
        return (self.authorization or "")[len("Bearer "):].strip()


class CreativeStudioApi:
    def __init__(self, settings: Settings, verifier: TokenVerifier | None = None, signer: StorageSigner | None = None):
        self.settings = settings
        self.verifier = verifier or TokenVerifier(settings)
        self.signer = signer or StorageSigner(settings)
        from .db import _connect as _db_connect
        self.assertion_guard = _assertion.AssertionGuard(settings, _db_connect)

    # -- WSGI ----------------------------------------------------------------
    def __call__(self, environ, start_response):
        req = Request(environ)
        try:
            status, payload = self.dispatch(req)
        except ServiceError as err:
            status, payload = err.status, err.body()
            if err.status >= 500:
                log.error("request failed: %s %s -> %s %s", req.method, req.path, err.code, err.detail)
        except Exception:
            log.exception("unhandled error: %s %s", req.method, req.path)
            status, payload = 500, {"error": "internal_error"}
        body = json.dumps(payload, default=_json_default).encode()
        start_response(f"{status} {_REASONS.get(status, 'Status')}",
                       [("Content-Type", "application/json"), ("Content-Length", str(len(body))),
                        ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")])
        return [body]

    def dispatch(self, req: Request):
        unknown = set(req.query) - _ALLOWED_QUERY
        if unknown:
            raise ServiceError(400, "unsupported_parameter", ",".join(sorted(unknown)))
        path_matched = False
        for method, pattern, name in _ROUTES:
            m = pattern.match(req.path)
            if not m:
                continue
            path_matched = True
            if method == req.method:
                self._assert_gateway(req, name, m.groups())
                return getattr(self, name)(req, *m.groups())
        if path_matched:
            raise ServiceError(405, "method_not_allowed")
        raise ServiceError(404, "not_found")

    def _assert_gateway(self, req: Request, handler: str, groups) -> None:
        """ENG-15: MOS gateway routes carry a signed service assertion next to the user's bearer."""
        policy = _assertion.POLICY.get(handler)
        if policy is None:
            return
        if not req._environ.get(_assertion.HEADER_ENV_KEY) and not policy.required:
            return                      # native Studio-UI call on a shared/read route
        actor = self.verifier.verify(req.authorization)
        self.assertion_guard.check(req, policy, actor=actor, workspace_id=_uuid(groups[0]))

    def _tx(self, req: Request):
        actor = self.verifier.verify(req.authorization)
        return actor, actor_transaction(self.settings.api_database_url, actor)

    @staticmethod
    def _require_read(cur, workspace_id: str) -> None:
        cur.execute("select cs_private.cs_can(%s, 'read') as ok", (workspace_id,))
        if not cur.fetchone()["ok"]:
            raise ServiceError(404, "resource_unavailable")

    # -- routes --------------------------------------------------------------
    @route("GET", r"/api/v1/health")
    def health(self, req):
        # Liveness reflects an actual round trip as the restricted runtime role.
        from .db import _connect
        conn = _connect(self.settings.api_database_url)
        try:
            with conn, conn.cursor() as cur:
                cur.execute("select current_user, count(*) from cs_private.cs_runtime_limits where environment_id = %s",
                            (self.settings.environment_id,))
                role, env_rows = cur.fetchone()
        finally:
            conn.close()
        if env_rows != 1:
            raise ServiceError(503, "environment_unavailable")
        return 200, {"status": "ready", "release_id": self.settings.release_id, "database_role": role}

    @route("GET", r"/api/v1/readiness")
    def readiness(self, req):
        # 1. DB round trip as cs_api
        from .db import _connect
        try:
            conn = _connect(self.settings.api_database_url)
            try:
                with conn, conn.cursor() as cur:
                    cur.execute("select current_user, count(*) from cs_private.cs_runtime_limits where environment_id = %s",
                                (self.settings.environment_id,))
                    role, env_rows = cur.fetchone()
            finally:
                conn.close()
            if env_rows != 1:
                raise ServiceError(503, "environment_unavailable", "no runtime limits row for environment")
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(503, "database_unavailable", str(exc)) from None

        # 2. Storage configuration & reachability
        if not self.settings.storage_url or not self.settings.storage_anon_key:
            raise ServiceError(503, "storage_unconfigured", "storage_url and storage_anon_key required")
        # The worker credential is proven, not assumed: real (cached) sign-in + authorised storage probe.
        from .config import ConfigError
        from .storage import make_storage_store
        try:
            make_storage_store(self.settings).probe()
        except ConfigError:
            raise ServiceError(503, "worker_identity_unconfigured") from None

        # 3. Renderer stack version check (refuse ready on mismatch)
        from .composition.renderer import renderer_version
        current_version = renderer_version()
        expected_prefix = "cs-image-compositor/1 pillow/12.2.0"
        if not current_version.startswith(expected_prefix):
            raise ServiceError(503, "renderer_version_mismatch",
                               f"expected prefix {expected_prefix!r}, got {current_version!r}")

        return 200, {
            "status": "ready",
            "release_id": self.settings.release_id,
            "database_role": role,
            "renderer_version": current_version,
            "storage": "configured",
        }

    @route("GET", r"/api/v1/me/workspaces")
    def my_workspaces(self, req):
        actor, tx = self._tx(req)
        with tx as cur:
            cur.execute("""
              select w.id, w.name, w.account_id, w.mode, m.role::text as role
                from public.cs_workspaces w
                left join public.cs_workspace_members m
                  on m.workspace_id = w.id and m.user_id = %s and m.revoked_at is null
               order by w.created_at, w.id""", (actor.user_id,))
            rows = cur.fetchall()
        return 200, {"workspaces": rows}

    @route("POST", r"/api/v1/accounts")
    def create_account(self, req):
        key, digest = req.require_idempotency()
        name = req.json().get("name")
        if not isinstance(name, str):
            raise ServiceError(422, "name_required")
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select * from public.cs_create_account(%s, %s, %s)", (name, key, digest))
            row = cur.fetchone()
        return (200 if row["replayed"] else 201), row

    @route("POST", rf"/api/v1/accounts/({_UUID})/workspaces")
    def create_workspace(self, req, account_id):
        account_id = _uuid(account_id)
        key, digest = req.require_idempotency()
        name = req.json().get("name")
        if not isinstance(name, str):
            raise ServiceError(422, "name_required")
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select * from public.cs_create_workspace(%s, %s, %s, %s)", (account_id, name, key, digest))
            row = cur.fetchone()
        return (200 if row["replayed"] else 201), row

    @route("PUT", rf"/api/v1/workspaces/({_UUID})/members/({_UUID})")
    def set_member(self, req, workspace_id, user_id):
        workspace_id, user_id = _uuid(workspace_id), _uuid(user_id)
        key, digest = req.require_idempotency()
        role = req.json().get("role")
        if role not in ("producer", "reviewer", "viewer"):
            raise ServiceError(422, "invalid_role")
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select * from public.cs_set_workspace_member(%s, %s, %s::public.cs_workspace_role, %s, %s)",
                        (workspace_id, user_id, role, key, digest))
            row = cur.fetchone()
        return 200, row

    @route("DELETE", rf"/api/v1/workspaces/({_UUID})/members/({_UUID})")
    def revoke_member(self, req, workspace_id, user_id):
        workspace_id, user_id = _uuid(workspace_id), _uuid(user_id)
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select public.cs_revoke_workspace_member(%s, %s) as revoked", (workspace_id, user_id))
            row = cur.fetchone()
        return 200, row

    @route("GET", rf"/api/v1/workspaces/({_UUID})/runs")
    def list_runs(self, req, workspace_id):
        workspace_id = _uuid(workspace_id)
        limit = _limit(req)
        _, tx = self._tx(req)
        with tx as cur:
            self._require_read(cur, workspace_id)
            cur.execute("""select id, state::text as state, event_sequence, cost_state::text as cost_state,
                                  failure_code, created_at, updated_at
                             from public.cs_runs where workspace_id = %s
                            order by created_at desc, id limit %s""", (workspace_id, limit))
            rows = cur.fetchall()
        return 200, {"runs": rows}

    @route("GET", rf"/api/v1/workspaces/({_UUID})/runs/({_UUID})")
    def get_run(self, req, workspace_id, run_id):
        workspace_id, run_id = _uuid(workspace_id), _uuid(run_id)
        after = _cursor(req)
        _, tx = self._tx(req)
        with tx as cur:
            self._require_read(cur, workspace_id)
            cur.execute("""select id, workspace_id, state::text as state, event_sequence,
                                  cost_state::text as cost_state, failure_code, created_at, updated_at
                             from public.cs_runs where workspace_id = %s and id = %s""", (workspace_id, run_id))
            run = cur.fetchone()
            if run is None:
                raise ServiceError(404, "resource_unavailable")
            cur.execute("""select node_id, state::text as state, attempt_number, output_refs, last_event_sequence
                             from public.cs_run_nodes where workspace_id = %s and run_id = %s
                            order by node_id""", (workspace_id, run_id))
            nodes = cur.fetchall()
            cur.execute("""select sequence, kind, payload, occurred_at from public.cs_run_events
                            where workspace_id = %s and run_id = %s and sequence > %s
                            order by sequence limit 500""", (workspace_id, run_id, after))
            events = cur.fetchall()
            run.update(_run_outcome(cur, workspace_id, run_id, run))
        return 200, {"run": run, "nodes": nodes, "events": events}

    @route("GET", rf"/api/v1/workspaces/({_UUID})/assets")
    def list_assets(self, req, workspace_id):
        workspace_id = _uuid(workspace_id)
        limit = _limit(req)
        _, tx = self._tx(req)
        with tx as cur:
            self._require_read(cur, workspace_id)
            cur.execute("""select a.id, a.label, a.kind, a.status, a.latest_version_id,
                                  v.revision, v.sha256, v.mime_type, v.size_bytes, v.width, v.height, v.duration_ms
                             from public.cs_assets a
                             left join public.cs_asset_versions v
                               on v.workspace_id = a.workspace_id and v.id = a.latest_version_id
                            where a.workspace_id = %s
                            order by a.created_at desc, a.id limit %s""", (workspace_id, limit))
            rows = cur.fetchall()
        return 200, {"assets": rows}

    @route("POST", rf"/api/v1/workspaces/({_UUID})/uploads")
    def create_upload(self, req, workspace_id):
        workspace_id = _uuid(workspace_id)
        key, digest = req.require_idempotency()
        body = req.json()
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select * from public.cs_create_upload_intent(%s, %s, %s, %s, %s, %s, %s, %s)",
                        (workspace_id, self.settings.environment_id, str(body.get("label") or ""),
                         body.get("mime_type"), body.get("size_bytes"), body.get("sha256"), key, digest))
            row = cur.fetchone()
        # upload_path is the bucket-relative exact object name the browser's
        # Storage RLS will accept for this user until expires_at. It is not a URL.
        return (200 if row["replayed"] else 201), {
            "upload_id": row["upload_id"], "asset_id": row["asset_id"],
            "asset_version_id": row["asset_version_id"], "bucket": "creative-studio",
            "upload_path": row["storage_key"], "expires_at": row["expires_at"], "replayed": row["replayed"]}

    @route("POST", rf"/api/v1/workspaces/({_UUID})/assets/({_UUID})/versions/({_UUID})/download")
    def create_download(self, req, workspace_id, asset_id, version_id):
        workspace_id, asset_id, version_id = _uuid(workspace_id), _uuid(asset_id), _uuid(version_id)
        expected_sha = req.json().get("sha256")
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select * from public.cs_create_download_grant(%s, %s, %s, %s, %s)",
                        (workspace_id, asset_id, version_id, expected_sha, self.settings.download_grant_seconds))
            grant = cur.fetchone()
        # Grant is committed; Storage now checks it under the user's own token.
        url = self.signer.sign_download(req.bearer_token(), grant["storage_key"], self.settings.signed_download_seconds)
        return 200, {"url": url, "expires_in": self.settings.signed_download_seconds,
                     "sha256": grant["sha256"], "mime_type": grant["mime_type"], "size_bytes": grant["size_bytes"]}


def _run_outcome(cur, workspace_id: str, run_id: str, run: dict) -> dict:
    """CS-17b: what happened after the provider returned, and the ACTUAL cost.

    `post_run` = typed per-canvas states (compose_blocked / exported / handoff_offered / handoff_delivered / ...).
    `handoff` = the exact MOS handoff reference once one exists (MOS pulls it by version_id).
    `actual_cost` is the settled provider cost (micros, USD) only when every reservation is settled; otherwise
    certainty is `unknown` and micros is null: an estimate or reservation is never reported as actual."""
    cur.execute("select public.cs_get_post_run_status(%s, %s) as v", (workspace_id, run_id))
    post = cur.fetchone()["v"]
    cur.execute("select public.cs_get_run_cost(%s, %s) as c", (workspace_id, run_id))
    cost = cur.fetchone()["c"]
    settled = cost["state"] == "settled" and run["state"] == "completed"
    out = {"post_run": post,
           "handoff": next((p["handoff"] for p in post if p.get("handoff")), None),
           "actual_cost": {"currency": cost["currency"], "micros": cost["settled_micros"] if settled else None,
                           "certainty": "settled" if settled else "unknown",
                           "unresolved_micros": cost["unresolved_micros"], "receipt_ids": cost["receipt_ids"]},
           "actual_usd": format(Decimal(cost["settled_micros"]) / Decimal(1_000_000), "f") if settled else None,
           "charge": None}
    if settled:
        cur.execute("select charge_certainty, provider_units from public.cs_list_run_receipts(%s, %s) "
                    "where kind in ('billing', 'reconciliation') and charge_certainty in "
                    "('provider_reported', 'balance_delta', 'estimated', 'unknown')", (workspace_id, run_id))
        rows = cur.fetchall()
        if rows:
            rank = ["unknown", "estimated", "balance_delta", "provider_reported"]
            weakest = min((r["charge_certainty"] for r in rows), key=lambda c: rank.index(c) if c in rank else 0)
            units = [r["provider_units"] for r in rows if r["provider_units"] is not None]
            out["charge"] = {"certainty": weakest, "credits": format(sum(units), "f") if units else None,
                             "scope": "call", "source": "creative_studio_provider_receipts"}
    return out


def _limit(req: Request) -> int:
    raw = req.query.get("limit", "50")
    if not raw.isdigit() or not 1 <= int(raw) <= 200:
        raise ServiceError(422, "invalid_limit")
    return int(raw)


def _cursor(req: Request) -> int:
    raw = req.query.get("after", "0")
    if not raw.isdigit():
        raise ServiceError(422, "invalid_cursor")
    return int(raw)


_REASONS = {200: "OK", 201: "Created", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
            404: "Not Found", 405: "Method Not Allowed", 409: "Conflict", 413: "Payload Too Large",
            422: "Unprocessable Entity", 500: "Internal Server Error", 502: "Bad Gateway",
            503: "Service Unavailable"}


def create_app(settings: Settings, **kwargs) -> CreativeStudioApi:
    return CreativeStudioApi(settings, **kwargs)


# --- Creative Studio domain routes (CS-B2 inputs, CS-B3 runtime, CS-B4 composition) ---
# One registration point (CS-INT). Each domain module owns its handlers and patterns;
# registration order is fixed and a domain module may neither replace an existing
# handler/attribute nor register a second handler for the same method + path pattern.
from .brand import routes as _cs_b2_brand_routes  # noqa: E402
from .composition import routes as _cs_b4_composition_routes  # noqa: E402
from .requests import routes as _cs_b2_request_routes  # noqa: E402
from .plans import routes as _cs16_plan_routes  # noqa: E402
from .postrun import routes as _cs17b_postrun_routes  # noqa: E402
from .runtime import routes as _cs_b3_runtime_routes  # noqa: E402

DOMAIN_ROUTE_MODULES = (_cs_b2_brand_routes, _cs_b2_request_routes, _cs_b3_runtime_routes,
                        _cs_b4_composition_routes, _cs16_plan_routes, _cs17b_postrun_routes)


def _register_domain_routes() -> None:
    before = dict(vars(CreativeStudioApi))
    for module in DOMAIN_ROUTE_MODULES:
        module.register(CreativeStudioApi, route)
    replaced = sorted(k for k, v in before.items() if vars(CreativeStudioApi).get(k) is not v)
    names = [name for _, _, name in _ROUTES]
    keys = [(method, pattern.pattern) for method, pattern, _ in _ROUTES]
    if replaced or len(set(names)) != len(names) or len(set(keys)) != len(keys):
        raise RuntimeError(f"creative studio route registration conflict: replaced={replaced}")


_register_domain_routes()
_REASONS.setdefault(202, "Accepted")
_REASONS.setdefault(428, "Precondition Required")
# --- end Creative Studio domain routes ---
