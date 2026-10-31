"""CS-B2 HTTP routes: creative requests, treatments, authoring drafts, MOS intake, compile mapping."""
from __future__ import annotations

import uuid

from service.errors import ServiceError

from ..brand.errors import InputRefusal
from ..brand.gateway import GatewayUnavailable
from ..brand.mos_brandkit import package_version_ref
from ..brand.routes import W, _UUID, gateway_of, refusal
from . import compile_mapping, contracts, documents, mos_intake, store


def _contract_error(exc: contracts.ContractViolation) -> ServiceError:
    code = "unsupported_schema_version" if exc.field_paths == ["/schema_version"] else "invalid_contract"
    return ServiceError(400 if code == "unsupported_schema_version" else 422, code, ",".join(exc.field_paths[:10]))


def _if_match(req) -> str | None:
    raw = (req._environ.get("HTTP_IF_MATCH") or "").strip().strip('"')
    return raw or None


def _derived(key: str, part: str) -> str:
    return str(uuid.uuid5(uuid.UUID(key), part))


def _package_ctx(cur, workspace_id: str, version_id: str) -> dict:
    row = store.get_package(cur, workspace_id, version_id)
    if row is None:
        raise ServiceError(404, "resource_unavailable")
    return {"document": row["payload"], "ref": package_version_ref(row["payload"]),
            "source_artifact_id": str(row["source_version_id"])}


def register(api_cls, route):
    from service.api import _uuid

    def guarded(fn):
        def wrapper(self, req, *args):
            try:
                return fn(self, req, *args)
            except InputRefusal as err:
                raise refusal(err) from None
            except contracts.ContractViolation as exc:
                raise _contract_error(exc) from None
        wrapper.__name__ = fn.__name__
        return wrapper

    @route("POST", W + r"/creative-requests/from-marketing-os")
    @guarded
    def b2_intake_marketing_os(self, req, workspace_id):
        """MOS visual job -> saved request + draft treatment + authoring draft, atomically."""
        workspace_id = _uuid(workspace_id)
        key, digest = req.require_idempotency()
        body = req.json()
        binding_id = _uuid(str(body.get("connection_id")))
        visual = body.get("visual_request")
        if not isinstance(visual, dict):
            raise ServiceError(422, "visual_request_required")
        actor, tx = self._tx(req)
        gw = gateway_of(self)
        with tx as cur:
            cur.execute("select external_workspace_id from public.cs_external_bindings where workspace_id = %s "
                        "and id = %s and status = 'active' and authority = 'marketing-os'", (workspace_id, binding_id))
            b = cur.fetchone()
            if b is None:
                raise ServiceError(404, "resource_unavailable")
            mos_ws = b["external_workspace_id"]
            draft_id = _derived(key, "authoring-draft")
            cur.execute("select payload from public.cs_authoring_drafts where workspace_id = %s and id = %s",
                        (workspace_id, draft_id))
            prior = cur.fetchone()
            if prior is not None:      # response loss: same key returns the same saved work
                sel = prior["payload"]["selections"]
                return 200, {"request": sel["request"], "treatment": sel["treatment"], "draft_id": draft_id,
                             "replayed": True}
            try:
                sources = gw.campaign_sources(mos_workspace_id=mos_ws, visual_request=visual)
            except GatewayUnavailable:
                raise ServiceError(502, "upstream_unavailable") from None
            if sources.get("workspace_id") != mos_ws or visual.get("workspace_id") != mos_ws:
                raise ServiceError(403, "scope_mismatch")
            pins = sources["content_pack"]["content"]["pins"]["value"]["brand_kit"]
            pkg_row = store.package_for_source(cur, workspace_id, binding_id, pins["id"])
            if pkg_row is None:
                raise ServiceError(409, "source_unavailable", "the BrandKit this ContentPack pins is not imported")
            package = {"document": pkg_row["payload"], "ref": package_version_ref(pkg_row["payload"]),
                       "source_artifact_id": str(pkg_row["source_version_id"])}
            fields = mos_intake.build_request_fields(visual, sources, package)
            try:
                receipt = gw.eligibility(mos_workspace_id=mos_ws, user_id=actor.user_id, action="generate",
                                         source_versions=[sources["concept"], sources["content_pack"]["ref"]]
                                         + fields["origin"]["copy_versions"])
            except GatewayUnavailable:
                raise ServiceError(502, "upstream_unavailable") from None
            fields["origin"]["generation_eligibility"] = receipt
            request = documents.new_request(fields, workspace_id=workspace_id, actor_id=actor.user_id)
            saved = store.save_request(cur, workspace_id, request, if_match=None, key=_derived(key, "request"),
                                       digest=digest)
            request_ref = documents.version_ref(request, "creative-request")
            treatment = documents.new_treatment(mos_intake.build_treatment_fields(sources, request_ref, package["ref"]),
                                                workspace_id=workspace_id, actor_id=actor.user_id)
            store.save_treatment(cur, workspace_id, treatment, if_match=None, key=_derived(key, "treatment"),
                                 digest=digest)
            treatment_ref = documents.version_ref(treatment, "treatment")
            draft = documents.new_authoring_draft(workspace_id=workspace_id, actor_id=actor.user_id,
                                                  name=f"{sources['offering']['name']} {visual.get('canvas')}")
            draft = {**draft, "id": draft_id}
            draft = documents.revised_draft({**draft, "revision": 0}, {
                "step": "direction",
                "selections": {"brand_package": package["ref"], "offering": sources["offering"]["source"],
                               "audiences": sources["audiences"], "concept": sources["concept"],
                               "request": request_ref, "treatment": treatment_ref}})
            store.save_draft(cur, workspace_id, draft, 0)
        return 201, {"request": request_ref, "request_semantic_sha256": documents.request_semantic(request),
                     "treatment": treatment_ref, "treatment_semantic_sha256": documents.treatment_semantic(treatment),
                     "draft_id": draft_id, "replayed": saved["replayed"]}

    @route("POST", W + r"/creative-requests")
    @guarded
    def b2_create_request(self, req, workspace_id):
        workspace_id = _uuid(workspace_id)
        key, digest = req.require_idempotency()
        actor, tx = self._tx(req)
        doc = documents.new_request(req.json(), workspace_id=workspace_id, actor_id=actor.user_id)
        with tx as cur:
            row = store.save_request(cur, workspace_id, doc, if_match=None, key=key, digest=digest)
        return (200 if row["replayed"] else 201), {"version_id": row["version_id"], "sha256": row["sha256"],
                                                   "replayed": row["replayed"]}

    @route("POST", W + rf"/creative-requests/({_UUID})/revisions")
    @guarded
    def b2_revise_request(self, req, workspace_id, object_id):
        workspace_id, object_id = _uuid(workspace_id), _uuid(object_id)
        key, digest = req.require_idempotency()
        actor, tx = self._tx(req)
        with tx as cur:
            head = store.get_version(cur, "cs_creative_request_versions", workspace_id, object_id)
            if head is None:
                raise ServiceError(404, "resource_unavailable")
            doc = documents.new_request(req.json(), workspace_id=workspace_id, actor_id=actor.user_id,
                                        object_id=object_id, revision=head["payload"]["revision"] + 1)
            row = store.save_request(cur, workspace_id, doc, if_match=_if_match(req), key=key, digest=digest)
        return (200 if row["replayed"] else 201), {"version_id": row["version_id"], "sha256": row["sha256"],
                                                   "replayed": row["replayed"]}

    @route("GET", W + rf"/creative-requests/({_UUID})/versions/({_UUID})")
    def b2_get_request(self, req, workspace_id, object_id, version_id):
        workspace_id, object_id, version_id = _uuid(workspace_id), _uuid(object_id), _uuid(version_id)
        _, tx = self._tx(req)
        with tx as cur:
            self._require_read(cur, workspace_id)
            row = store.get_version(cur, "cs_creative_request_versions", workspace_id, object_id, version_id)
        if row is None:
            raise ServiceError(404, "resource_unavailable")
        return 200, {"request": row["payload"], "semantic_sha256": row["semantic_sha256"]}

    @route("POST", W + r"/treatments")
    @guarded
    def b2_create_treatment(self, req, workspace_id):
        workspace_id = _uuid(workspace_id)
        key, digest = req.require_idempotency()
        actor, tx = self._tx(req)
        doc = documents.new_treatment(req.json(), workspace_id=workspace_id, actor_id=actor.user_id)
        with tx as cur:
            row = store.save_treatment(cur, workspace_id, doc, if_match=None, key=key, digest=digest)
        return (200 if row["replayed"] else 201), {"version_id": row["version_id"], "sha256": row["sha256"],
                                                   "id": doc["id"], "replayed": row["replayed"]}

    @route("POST", W + rf"/treatments/({_UUID})/revisions")
    @guarded
    def b2_revise_treatment(self, req, workspace_id, object_id):
        workspace_id, object_id = _uuid(workspace_id), _uuid(object_id)
        key, digest = req.require_idempotency()
        actor, tx = self._tx(req)
        with tx as cur:
            head = store.get_version(cur, "cs_treatment_versions", workspace_id, object_id)
            if head is None:
                raise ServiceError(404, "resource_unavailable")
            doc = documents.new_treatment(req.json(), workspace_id=workspace_id, actor_id=actor.user_id,
                                          object_id=object_id, revision=head["payload"]["revision"] + 1)
            row = store.save_treatment(cur, workspace_id, doc, if_match=_if_match(req), key=key, digest=digest)
        return (200 if row["replayed"] else 201), {"version_id": row["version_id"], "sha256": row["sha256"],
                                                   "replayed": row["replayed"]}

    @route("POST", W + rf"/treatments/({_UUID})/accept")
    @guarded
    def b2_accept_treatment(self, req, workspace_id, object_id):
        workspace_id, object_id = _uuid(workspace_id), _uuid(object_id)
        key, digest = req.require_idempotency()
        version = req.json().get("version") or {}
        actor, tx = self._tx(req)
        with tx as cur:
            head = store.get_version(cur, "cs_treatment_versions", workspace_id, object_id)
            if head is None:
                raise ServiceError(404, "resource_unavailable")
            draft = head["payload"]
            if (version.get("id"), version.get("version_id"), version.get("sha256")) != \
                    (draft["id"], draft["version_id"], draft["sha256"]) or _if_match(req) != draft["sha256"]:
                cur.execute("select cs_private.cs_can(%s,'produce') as ok", (workspace_id,))
                if not cur.fetchone()["ok"]:
                    raise ServiceError(403, "capability_denied")
                raise ServiceError(409, "revision_conflict", draft["version_id"])
            doc = documents.accepted_treatment(draft, actor_id=actor.user_id)
            row = store.accept_treatment(cur, workspace_id, doc, if_match=draft["sha256"], key=key, digest=digest)
        return (200 if row["replayed"] else 201), {
            "treatment": {**documents.version_ref(doc, "treatment"), "version_id": str(row["version_id"]),
                          "sha256": row["sha256"]},
            "semantic_sha256": head["semantic_sha256"], "replayed": row["replayed"]}

    @route("GET", W + rf"/treatments/({_UUID})/versions/({_UUID})")
    def b2_get_treatment(self, req, workspace_id, object_id, version_id):
        workspace_id, object_id, version_id = _uuid(workspace_id), _uuid(object_id), _uuid(version_id)
        _, tx = self._tx(req)
        with tx as cur:
            self._require_read(cur, workspace_id)
            row = store.get_version(cur, "cs_treatment_versions", workspace_id, object_id, version_id)
        if row is None:
            raise ServiceError(404, "resource_unavailable")
        return 200, {"treatment": row["payload"], "semantic_sha256": row["semantic_sha256"]}

    @route("POST", W + r"/authoring-drafts")
    @guarded
    def b2_create_draft(self, req, workspace_id):
        workspace_id = _uuid(workspace_id)
        name = req.json().get("name")
        if not isinstance(name, str) or not name.strip():
            raise ServiceError(422, "name_required")
        actor, tx = self._tx(req)
        doc = documents.new_authoring_draft(workspace_id=workspace_id, actor_id=actor.user_id, name=name.strip()[:200])
        with tx as cur:
            row = store.save_draft(cur, workspace_id, doc, 0)
        return 201, {"draft": doc, "revision": row["revision"]}

    @route("GET", W + rf"/authoring-drafts/({_UUID})")
    def b2_get_draft(self, req, workspace_id, draft_id):
        workspace_id, draft_id = _uuid(workspace_id), _uuid(draft_id)
        _, tx = self._tx(req)
        with tx as cur:
            self._require_read(cur, workspace_id)
            cur.execute("select payload from public.cs_authoring_drafts where workspace_id = %s and id = %s",
                        (workspace_id, draft_id))
            row = cur.fetchone()
        if row is None:
            raise ServiceError(404, "resource_unavailable")
        return 200, {"draft": row["payload"]}

    @route("PUT", W + rf"/authoring-drafts/({_UUID})")
    @guarded
    def b2_save_draft(self, req, workspace_id, draft_id):
        workspace_id, draft_id = _uuid(workspace_id), _uuid(draft_id)
        _, tx = self._tx(req)
        with tx as cur:
            self._require_read(cur, workspace_id)
            cur.execute("select payload from public.cs_authoring_drafts where workspace_id = %s and id = %s",
                        (workspace_id, draft_id))
            row = cur.fetchone()
            if row is None:
                raise ServiceError(404, "resource_unavailable")
            current = row["payload"]
            if _if_match(req) != current["sha256"]:
                raise ServiceError(409, "revision_conflict", current["sha256"])
            doc = documents.revised_draft(current, req.json())
            saved = store.save_draft(cur, workspace_id, doc, current["revision"])
        return 200, {"draft": doc, "revision": saved["revision"]}

    @route("POST", W + rf"/creative-requests/({_UUID})/versions/({_UUID})/compile-mapping")
    @guarded
    def b2_compile_mapping(self, req, workspace_id, object_id, version_id):
        """Preview of the compiler inputs for an exact request + accepted treatment (no plan, no provider call)."""
        workspace_id, object_id, version_id = _uuid(workspace_id), _uuid(object_id), _uuid(version_id)
        tref = req.json().get("treatment") or {}
        _, tx = self._tx(req)
        with tx as cur:
            self._require_read(cur, workspace_id)
            r = store.get_version(cur, "cs_creative_request_versions", workspace_id, object_id, version_id)
            t = store.get_version(cur, "cs_treatment_versions", workspace_id, _uuid(str(tref.get("id"))),
                                  _uuid(str(tref.get("version_id"))))
            if r is None or t is None:
                raise ServiceError(404, "resource_unavailable")
            pkg = _package_ctx(cur, workspace_id, r["payload"]["brand_package"]["version_id"])
            cur.execute("select public.cs_brand_package_eligibility(%s,%s,'author',null) as e",
                        (workspace_id, pkg["document"]["version_id"]))
            elig = cur.fetchone()["e"]
        if elig["decision"] != "allowed":
            raise ServiceError(409, elig["reason_codes"][0])
        return 200, compile_mapping.compile_inputs(r["payload"], t["payload"], pkg["document"])

    for fn in (b2_intake_marketing_os, b2_create_request, b2_revise_request, b2_get_request, b2_create_treatment,
               b2_revise_treatment, b2_accept_treatment, b2_get_treatment, b2_create_draft, b2_get_draft,
               b2_save_draft, b2_compile_mapping):
        setattr(api_cls, fn.__name__, fn)
