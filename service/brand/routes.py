"""CS-B2 HTTP routes: connections, brand package import operations, package reads."""
from __future__ import annotations

import json

from service.errors import ServiceError
from service.requests import contracts

from .errors import InputRefusal
from .gateway import GatewayUnavailable

_UUID = r"[0-9a-fA-F-]{36}"
W = rf"/api/v1/workspaces/({_UUID})"
ACTIONS = ("author", "generate", "export", "handoff")


def gateway_of(api):
    gw = getattr(api, "mos_gateway", None)
    if gw is None:
        if getattr(api, "settings", None) and api.settings.mos_gateway_url:
            from ..runtime.gateway import HttpMarketingOsGateway
            gw = HttpMarketingOsGateway(api.settings.mos_gateway_url, api.settings.mos_service_token)
            api.mos_gateway = gw
            return gw
        raise ServiceError(503, "upstream_unavailable", "no Marketing OS gateway is configured")
    return gw


def refusal(err: InputRefusal) -> ServiceError:
    return ServiceError(err.status, err.code, err.message[:300] + (
        (" @ " + ",".join(err.field_paths[:5])) if err.field_paths else ""))


def operation_document(op: dict) -> dict:
    """cs-operation-v1 projection (no lease, payload or secret)."""
    doc = {"schema_version": "cs-operation-v1", "id": str(op["id"]), "workspace_id": str(op["workspace_id"]),
           "kind": op["kind"], "state": op["state"], "created_by": str(op["created_by"]),
           "created_at": op["created_at"].isoformat().replace("+00:00", "Z"),
           "updated_at": op["updated_at"].isoformat().replace("+00:00", "Z"),
           "idempotency_key": str(op["idempotency_key"]), "request_sha256": op["request_sha256"],
           "event_sequence": op["event_sequence"], "input_refs": op["input_refs"], "result_refs": op["result_refs"],
           "result_asset_refs": op["result_asset_refs"], "attempts": [], "receipt_ids": [],
           "admission": op["admission"], "cost": op["cost"], "error": op["error"]}
    contracts.validate(doc, "cs-operation-v1")
    return doc


def register(api_cls, route):
    from service.api import _uuid   # B1 helper

    def body_uuid(body, name):
        try:
            return _uuid(str(body.get(name)))
        except ServiceError:
            raise ServiceError(422, f"{name}_required") from None

    @route("POST", W + r"/connections")
    def b2_create_connection(self, req, workspace_id):
        workspace_id = _uuid(workspace_id)
        key, digest = req.require_idempotency()
        body = req.json()
        if body.get("authority") != "marketing-os":
            raise ServiceError(422, "unsupported_authority")
        external = body_uuid(body, "external_workspace_id")
        actor, tx = self._tx(req)
        try:
            verification = gateway_of(self).authorize_binding(user_id=actor.user_id, mos_workspace_id=external)
        except GatewayUnavailable:
            raise ServiceError(502, "upstream_unavailable") from None
        with tx as cur:
            cur.execute("select * from public.cs_bind_external_workspace(%s, 'marketing-os', %s, %s, %s, %s)",
                        (workspace_id, external, json.dumps(verification), key, digest))
            row = cur.fetchone()
        return (200 if row["replayed"] else 201), {"connection_id": row["binding_id"], "replayed": row["replayed"]}

    @route("GET", W + r"/connections")
    def b2_list_connections(self, req, workspace_id):
        workspace_id = _uuid(workspace_id)
        _, tx = self._tx(req)
        with tx as cur:
            self._require_read(cur, workspace_id)
            cur.execute("""select id as connection_id, authority::text as authority, external_workspace_id, status,
                                  created_at from public.cs_external_bindings where workspace_id = %s
                            order by created_at""", (workspace_id,))
            rows = cur.fetchall()
        return 200, {"connections": rows}

    @route("POST", W + rf"/connections/({_UUID})/refresh")
    def b2_refresh_connection(self, req, workspace_id, binding_id):
        """Re-read MOS's current BrandKit head for the last imported package; record it; report eligibility."""
        workspace_id, binding_id = _uuid(workspace_id), _uuid(binding_id)
        _, tx = self._tx(req)
        with tx as cur:
            self._require_read(cur, workspace_id)
            cur.execute("""select b.external_workspace_id, s.source_version_id, s.version_id
                             from public.cs_external_bindings b
                             left join lateral (select * from public.cs_source_imports i
                                                 where i.workspace_id = b.workspace_id and i.binding_id = b.id
                                                 order by i.revision desc limit 1) s on true
                            where b.workspace_id = %s and b.id = %s""", (workspace_id, binding_id))
            row = cur.fetchone()
            if row is None:
                raise ServiceError(404, "resource_unavailable")
            if row["source_version_id"] is None:
                return 200, {"current": None, "eligibility": None}
            try:
                export = gateway_of(self).export_brand_kit(mos_workspace_id=row["external_workspace_id"],
                                                           artifact_id=str(row["source_version_id"]))
            except GatewayUnavailable:
                raise ServiceError(502, "upstream_unavailable") from None
            cur_head = export["current"]
            cur.execute("select public.cs_record_source_head(%s,%s,'BrandKit',%s,%s,%s)",
                        (workspace_id, binding_id, cur_head["version_id"], cur_head["revision"], cur_head["status"]))
            cur.execute("select public.cs_brand_package_eligibility(%s,%s,'generate',null) as e",
                        (workspace_id, row["version_id"]))
            elig = cur.fetchone()["e"]
        return 200, {"current": cur_head, "eligibility": elig}

    @route("POST", W + r"/brand-packages/imports")
    def b2_import_brand_package(self, req, workspace_id):
        workspace_id = _uuid(workspace_id)
        key, digest = req.require_idempotency()
        body = req.json()
        binding = body_uuid(body, "connection_id")
        package = body.get("package")
        try:
            contracts.validate_def(package, "version_ref")
        except contracts.ContractViolation as exc:
            raise ServiceError(422, "invalid_package_ref", ",".join(exc.field_paths)) from None
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select * from public.cs_admit_brand_import(%s,%s,%s,%s,%s,%s,%s)",
                        (workspace_id, binding, json.dumps(package), self.settings.environment_id,
                         self.settings.release_id, key, digest))
            row = cur.fetchone()
        return (200 if row["replayed"] else 202), {
            "operation_id": row["operation_id"], "state": row["state"], "replayed": row["replayed"],
            "location": f"/api/v1/workspaces/{workspace_id}/operations/{row['operation_id']}"}

    @route("GET", W + rf"/operations/({_UUID})")
    def b2_get_operation(self, req, workspace_id, operation_id):
        workspace_id, operation_id = _uuid(workspace_id), _uuid(operation_id)
        _, tx = self._tx(req)
        with tx as cur:
            self._require_read(cur, workspace_id)
            cur.execute("""select id, workspace_id, kind::text as kind, state::text as state, created_by, created_at,
                                  updated_at, idempotency_key, request_sha256, event_sequence, input_refs, result_refs,
                                  result_asset_refs, admission, cost, error
                             from public.cs_operations where workspace_id = %s and id = %s""",
                        (workspace_id, operation_id))
            op = cur.fetchone()
            if op is None:
                raise ServiceError(404, "resource_unavailable")
            cur.execute("""select sequence, kind, payload, occurred_at from public.cs_operation_events
                            where workspace_id = %s and operation_id = %s order by sequence""",
                        (workspace_id, operation_id))
            events = cur.fetchall()
        return 200, {"operation": operation_document(dict(op)), "events": events}

    @route("GET", W + rf"/brand-packages/({_UUID})")
    def b2_get_brand_package(self, req, workspace_id, version_id):
        workspace_id, version_id = _uuid(workspace_id), _uuid(version_id)
        _, tx = self._tx(req)
        with tx as cur:
            self._require_read(cur, workspace_id)
            cur.execute("select payload from public.cs_source_imports where workspace_id = %s and version_id = %s",
                        (workspace_id, version_id))
            row = cur.fetchone()
            if row is None:
                raise ServiceError(404, "resource_unavailable")
            elig = {}
            for action in ACTIONS:
                cur.execute("select public.cs_brand_package_eligibility(%s,%s,%s,null) as e",
                            (workspace_id, version_id, action))
                elig[action] = cur.fetchone()["e"]
        return 200, {"package": row["payload"], "eligibility": elig}

    for fn in (b2_create_connection, b2_list_connections, b2_refresh_connection, b2_import_brand_package,
               b2_get_operation, b2_get_brand_package):
        setattr(api_cls, fn.__name__, fn)
