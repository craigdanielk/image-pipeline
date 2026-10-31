"""CS-16 route: POST /api/v1/workspaces/{W}/creative-requests/{rid}/compile (native + gateway action plan.compile).

body: {"treatment": {"id", "version_id"}, "tier": "draft"|"final" (default draft)}
Runs the deterministic compiler in-process over the frozen treatment, the imported approved brand package and the
current provider catalogue; persists an immutable hash-pinned plan (idempotent per exact inputs) and supersedes older
plans of the same request. No provider call, no network, no spend.
"""
from __future__ import annotations

from ..brand.errors import InputRefusal
from ..brand.routes import W, _UUID, refusal
from ..errors import ServiceError
from ..requests import store as rstore
from . import compiler, store

CONTRACT_ERRORS = None


def register(api_cls, route):
    from service.api import _uuid

    @route("POST", W + rf"/creative-requests/({_UUID})/compile")
    def cs16_compile_plan(self, req, workspace_id, object_id):
        workspace_id, object_id = _uuid(workspace_id), _uuid(object_id)
        body = req.json()
        tref = body.get("treatment")
        if not isinstance(tref, dict) or not tref.get("id") or not tref.get("version_id"):
            raise ServiceError(422, "treatment_required", "body.treatment {id, version_id} is required")
        tier = body.get("tier", "draft")
        if tier not in ("draft", "final"):
            raise ServiceError(422, "tier_required", "tier must be draft or final")
        _, tx = self._tx(req)
        with tx as cur:
            self._require_read(cur, workspace_id)
            r = rstore.get_version(cur, "cs_creative_request_versions", workspace_id, object_id)
            if r is None:
                raise ServiceError(404, "resource_unavailable")
            t = rstore.get_version(cur, "cs_treatment_versions", workspace_id, _uuid(str(tref["id"])),
                                   _uuid(str(tref["version_id"])))
            if t is None:
                raise ServiceError(404, "resource_unavailable")
            request, treatment = r["payload"], t["payload"]
            if treatment.get("status") != "accepted":
                raise ServiceError(409, "treatment_not_frozen", "only an accepted treatment revision compiles")
            pkg = rstore.get_package(cur, workspace_id, request["brand_package"]["version_id"])
            if pkg is None:
                raise ServiceError(409, "brand_package_missing", "the request's brand package is not imported")
            elig = rstore.eligibility(cur, workspace_id, pkg["payload"]["version_id"], "author")
            if elig["decision"] != "allowed":
                raise ServiceError(409, "brand_package_missing", elig["reason_codes"][0])
            package = pkg["payload"]
            entries = store.catalogue(cur)
            try:
                probe = compiler.compile_plan(request=request, treatment=treatment, package=package, catalogue=entries,
                                              tier=tier, revision=1)
                ckey = probe[2]
                found = store.lookup(cur, workspace_id, object_id, ckey)
                if found["existing"]:
                    return 200, _response(found["payload"], found["summary"], True, found.get("superseded_by"))
                plan, summary, ckey, used = compiler.compile_plan(
                    request=request, treatment=treatment, package=package, catalogue=entries, tier=tier,
                    revision=found["next_revision"])
            except InputRefusal as err:
                raise refusal(err) from None
            saved = store.save(cur, workspace_id, plan, summary, request, treatment, package["version_id"],
                               plan["program"]["catalogue_sha256"], ckey, tier)
        return (200 if saved["replayed"] else 201), _response(plan, summary, saved["replayed"], None)

    @route("GET", W + rf"/execution-plans/({_UUID})")
    def cs16_get_plan(self, req, workspace_id, plan_id):
        workspace_id = _uuid(workspace_id)
        _, tx = self._tx(req)
        with tx as cur:
            row = store.get(cur, workspace_id, plan_id)
        return 200, _response(row["payload"], row["summary"], False, row["superseded_by"])

    for fn in (cs16_compile_plan, cs16_get_plan):
        setattr(api_cls, fn.__name__, fn)


def _response(plan: dict, summary: dict, replayed: bool, superseded_by) -> dict:
    return {"plan_id": plan["id"], "plan_sha256": plan["sha256"], "compile_hash": plan["compile_hash"],
            "revision": plan["revision"], "status": "superseded" if superseded_by else "current",
            "superseded_by": superseded_by, "replayed": replayed, "media_pack": summary, "plan": plan}
