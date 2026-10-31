"""CS-B4 HTTP routes (registered from one fenced block in service/api.py).

Composition save/revise/read, export read, MOS handoff enqueue/read and a
side-effect-free composition validation. Every route verifies the bearer
token, then runs one cs_api transaction where SQL rechecks current
membership. Responses follow endpoint-outline.md: {"data": ...}.
Rendering is not done in the API process: it is the compose_image /
publish_export worker stage (bytes come from private storage, never a URL).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from ..errors import ServiceError
from .canonical import artifact_sha256
from .errors import CompositionError
from .handoff import build_handoff
from .validate import semantic_sha256, validate_composition

_UUID = r"[0-9a-fA-F-]{36}"
_EDITABLE = {"kind", "basis_recipe", "basis_run", "artboards", "video"}


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError):
        raise ServiceError(404, "resource_unavailable") from None


def _refusal(err: CompositionError) -> ServiceError:
    return ServiceError(err.http_status, err.code, err.message)


def _editable(body: dict) -> dict:
    unknown = sorted(set(body) - _EDITABLE)
    if unknown:
        raise ServiceError(422, "unsupported_field", ",".join(unknown))
    missing = sorted(_EDITABLE - set(body))
    if missing:
        raise ServiceError(422, "invalid_composition", "missing " + ",".join(missing))
    return {k: body[k] for k in sorted(_EDITABLE)}


def _input_refs(doc: dict) -> list[dict]:
    refs, seen = [], set()

    def add(ref):
        if ref and ref["asset_version_id"] not in seen:
            seen.add(ref["asset_version_id"])
            refs.append(ref)
    for board in doc["artboards"]:
        for layer in board["layers"]:
            content = layer["content"]
            add(content.get("asset"))
            add(content.get("font"))
    video = doc.get("video") or {}
    for shot in video.get("shots", []):
        add(shot["source"])
    for cap in video.get("captions", []):
        add(cap["font_ref"])
    for track in video.get("audio", []):
        add(track["source"])
    for overlay in video.get("overlays", []):
        add(overlay["layer"]["content"].get("asset"))
        add(overlay["layer"]["content"].get("font"))
    return refs


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def register(api_cls, route) -> None:
    def _save(self, req, cur, workspace_id, doc, expected_sha):
        try:
            doc["sha256"] = artifact_sha256(doc)
            validate_composition(doc)
        except CompositionError as err:
            raise _refusal(err) from None
        key, digest = req.require_idempotency()
        cur.execute("select * from public.cs_save_composition_version(%s, %s::jsonb, %s, %s::jsonb, %s, %s, %s)",
                    (workspace_id, json.dumps(doc), semantic_sha256(doc), json.dumps(_input_refs(doc)),
                     expected_sha, key, digest))
        row = cur.fetchone()
        if row["replayed"]:
            cur.execute("select public.cs_get_composition_version(%s, null, %s) as payload",
                        (workspace_id, row["version_id"]))
            return 200, {"data": cur.fetchone()["payload"]}
        return 201, {"data": doc}

    @route("POST", rf"/api/v1/workspaces/({_UUID})/compositions")
    def cs_b4_create_composition(self, req, workspace_id):
        workspace_id = _uuid(workspace_id)
        actor, tx = self._tx(req)
        fields = _editable(req.json())
        req.require_idempotency()
        doc = {"schema_version": "cs-composition-v1", "id": str(uuid.uuid4()), "version_id": str(uuid.uuid4()),
               "revision": 1, "workspace_id": workspace_id, "created_at": _now(), "created_by": actor.user_id,
               "sha256": "", **fields}
        with tx as cur:
            return _save(self, req, cur, workspace_id, doc, None)

    @route("POST", rf"/api/v1/workspaces/({_UUID})/compositions/({_UUID})/revisions")
    def cs_b4_revise_composition(self, req, workspace_id, composition_id):
        workspace_id, composition_id = _uuid(workspace_id), _uuid(composition_id)
        actor, tx = self._tx(req)
        if_match = (req._environ.get("HTTP_IF_MATCH") or "").strip().strip('"')
        if not if_match:
            raise ServiceError(428, "if_match_required")
        fields = _editable(req.json())
        req.require_idempotency()
        with tx as cur:
            cur.execute("select public.cs_get_composition_version(%s, %s, null) as payload",
                        (workspace_id, composition_id))
            current = cur.fetchone()["payload"]
            doc = {"schema_version": "cs-composition-v1", "id": composition_id, "version_id": str(uuid.uuid4()),
                   "revision": current["revision"] + 1, "workspace_id": workspace_id, "created_at": _now(),
                   "created_by": actor.user_id, "sha256": "", **fields}
            return _save(self, req, cur, workspace_id, doc, if_match)

    @route("GET", rf"/api/v1/workspaces/({_UUID})/compositions/({_UUID})/versions/({_UUID})")
    def cs_b4_get_composition(self, req, workspace_id, composition_id, version_id):
        workspace_id, composition_id, version_id = _uuid(workspace_id), _uuid(composition_id), _uuid(version_id)
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select public.cs_get_composition_version(%s, %s, %s) as payload",
                        (workspace_id, composition_id, version_id))
            return 200, {"data": cur.fetchone()["payload"]}

    @route("GET", rf"/api/v1/workspaces/({_UUID})/compositions/({_UUID})")
    def cs_b4_get_latest_composition(self, req, workspace_id, composition_id):
        workspace_id, composition_id = _uuid(workspace_id), _uuid(composition_id)
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select public.cs_get_composition_version(%s, %s, null) as payload",
                        (workspace_id, composition_id))
            return 200, {"data": cur.fetchone()["payload"]}

    @route("POST", rf"/api/v1/workspaces/({_UUID})/compositions/validate")
    def cs_b4_validate_composition(self, req, workspace_id):
        """Structural + semantic issues for an unsaved draft; saves nothing, renders nothing."""
        workspace_id = _uuid(workspace_id)
        actor, tx = self._tx(req)
        fields = _editable(req.json())
        with tx as cur:
            self._require_read(cur, workspace_id)
        doc = {"schema_version": "cs-composition-v1", "id": str(uuid.UUID(int=0)), "version_id": str(uuid.UUID(int=0)),
               "revision": 1, "workspace_id": workspace_id, "created_at": "2000-01-01T00:00:00Z",
               "created_by": actor.user_id, "sha256": "", **fields}
        try:
            validate_composition(doc, check_hash=False)
        except CompositionError as err:
            return 200, {"data": {"valid": False, "issues": [err.to_error()]}}
        return 200, {"data": {"valid": True, "issues": [], "semantic_sha256": semantic_sha256(doc)}}

    @route("GET", rf"/api/v1/workspaces/({_UUID})/exports/({_UUID})")
    def cs_b4_get_export(self, req, workspace_id, version_id):
        workspace_id, version_id = _uuid(workspace_id), _uuid(version_id)
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select public.cs_get_export(%s, %s, null) as manifest", (workspace_id, version_id))
            return 200, {"data": cur.fetchone()["manifest"]}

    @route("POST", rf"/api/v1/workspaces/({_UUID})/handoffs/marketing-os")
    def cs_b4_create_handoff(self, req, workspace_id):
        workspace_id = _uuid(workspace_id)
        actor, tx = self._tx(req)
        body = req.json()
        if set(body) != {"exports", "target", "creative_request"} or not isinstance(body.get("exports"), list):
            raise ServiceError(422, "invalid_mos_handoff", "body is {exports, target, creative_request}")
        key, digest = req.require_idempotency()
        with tx as cur:
            self._require_read(cur, workspace_id)
            exports = []
            for ref in body["exports"]:
                if not isinstance(ref, dict) or ref.get("workspace_id") != workspace_id:
                    raise ServiceError(403, "scope_mismatch")
                cur.execute("select public.cs_get_export(%s, %s, %s) as manifest",
                            (workspace_id, _uuid(str(ref.get("version_id"))), str(ref.get("sha256"))))
                exports.append(cur.fetchone()["manifest"])
            try:
                doc = build_handoff(exports, target=body["target"], creative_request=body["creative_request"],
                                    idempotency_key=key, created_by=actor.user_id)
            except (CompositionError, KeyError, TypeError) as err:
                if isinstance(err, CompositionError):
                    raise _refusal(err) from None
                raise ServiceError(422, "invalid_mos_handoff") from None
            cur.execute("select * from public.cs_enqueue_handoff(%s, %s::jsonb, %s, %s)",
                        (workspace_id, json.dumps(doc), key, digest))
            row = cur.fetchone()
            cur.execute("select * from public.cs_get_handoff(%s, %s)", (workspace_id, row["handoff_id"]))
            stored = cur.fetchone()
        return (200 if row["replayed"] else 202), {"data": {"handoff": stored["payload"], "state": stored["state"],
                                                            "receipt": None}}

    @route("GET", rf"/api/v1/workspaces/({_UUID})/handoffs/marketing-os/({_UUID})")
    def cs_b4_get_handoff(self, req, workspace_id, handoff_id):
        workspace_id, handoff_id = _uuid(workspace_id), _uuid(handoff_id)
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select * from public.cs_get_handoff(%s, %s)", (workspace_id, handoff_id))
            row = cur.fetchone()
        return 200, {"data": {"handoff": row["payload"], "state": row["state"], "receipt": row["receipt_ref"]}}

    for fn in (cs_b4_create_composition, cs_b4_revise_composition, cs_b4_get_composition,
               cs_b4_get_latest_composition,
               cs_b4_validate_composition, cs_b4_get_export, cs_b4_create_handoff, cs_b4_get_handoff):
        setattr(api_cls, fn.__name__, fn)
