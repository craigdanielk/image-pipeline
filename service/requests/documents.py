"""Server-side stamping of saved Creative Studio documents.

Callers supply editable fields only; identity, revision, audit fields and the
RFC 8785 digest are assigned here (and re-checked in SQL). Every document is
validated against its bundled contract schema before it is sent to the database.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from . import contracts
from .canonical import artifact_preimage, artifact_sha256, canonical_text, request_semantic_sha256, \
    treatment_semantic_sha256

REQUEST_EDITABLE = ("origin", "brand_package", "offering", "audiences", "objective", "hook", "locale", "exact_copy",
                    "formats", "references", "provider_selection", "budget", "constraints")
REQUEST_OPTIONAL = ("shot_spec",)   # catalogue shot (mos-shot-spec-v1); absent on legacy prose-direction requests
TREATMENT_EDITABLE = ("source_request", "authorship", "direction", "shots", "sources")


def now_utc() -> str:
    at = datetime.now(timezone.utc)
    return at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{at.microsecond // 1000:03d}Z"


def _finish(doc: dict) -> dict:
    doc["sha256"] = artifact_sha256(doc)
    contracts.validate(doc)
    return doc


def canonical_for_db(doc: dict) -> str:
    """Canonical preimage text the database binds to doc['sha256']."""
    return canonical_text(artifact_preimage(doc))


def new_request(fields: dict, *, workspace_id: str, actor_id: str, object_id: str | None = None,
                revision: int = 1) -> dict:
    unknown = set(fields) - set(REQUEST_EDITABLE) - set(REQUEST_OPTIONAL)
    if unknown:
        raise contracts.ContractViolation("cs-creative-request-v1", [(f"/{k}", "not an editable field")
                                                                     for k in sorted(unknown)])
    doc = {"schema_version": "cs-creative-request-v1", "id": object_id or str(uuid.uuid4()),
           "version_id": str(uuid.uuid4()), "revision": revision, "workspace_id": workspace_id,
           "created_at": now_utc(), "created_by": actor_id, "sha256": "0" * 64}
    doc.update({k: fields[k] for k in REQUEST_EDITABLE + REQUEST_OPTIONAL if k in fields})
    return _finish(doc)


def new_treatment(fields: dict, *, workspace_id: str, actor_id: str, object_id: str | None = None,
                  revision: int = 1) -> dict:
    unknown = set(fields) - set(TREATMENT_EDITABLE)
    if unknown:
        raise contracts.ContractViolation("cs-treatment-v1", [(f"/{k}", "not an editable field")
                                                              for k in sorted(unknown)])
    doc = {"schema_version": "cs-treatment-v1", "id": object_id or str(uuid.uuid4()),
           "version_id": str(uuid.uuid4()), "revision": revision, "workspace_id": workspace_id,
           "created_at": now_utc(), "created_by": actor_id, "sha256": "0" * 64, "status": "draft",
           "accepted_by": None, "accepted_at": None}
    doc.update({k: fields[k] for k in TREATMENT_EDITABLE if k in fields})
    return _finish(doc)


def accepted_treatment(draft: dict, *, actor_id: str) -> dict:
    """Next revision of the reviewed draft, frozen with the accepting actor and time."""
    at = now_utc()
    doc = {k: v for k, v in draft.items() if k != "sha256"}
    doc.update({"version_id": str(uuid.uuid4()), "revision": draft["revision"] + 1, "created_at": at,
                "created_by": actor_id, "status": "accepted", "accepted_by": actor_id, "accepted_at": at,
                "sha256": "0" * 64})
    return _finish(doc)


def new_authoring_draft(*, workspace_id: str, actor_id: str, name: str) -> dict:
    at = now_utc()
    doc = {"schema_version": "cs-authoring-draft-v1", "id": str(uuid.uuid4()), "workspace_id": workspace_id,
           "revision": 1, "sha256": "0" * 64, "created_by": actor_id, "created_at": at, "updated_at": at,
           "name": name, "step": "brand", "status": "editing", "base_recipe": None,
           "selections": {"brand_package": None, "offering": None, "audiences": [], "concept": None,
                          "request": None, "treatment": None},
           "pending_fields": {}, "working_nodes": [],
           "authoring_view": {"last_view": "guided", "positions": [], "collapsed_node_ids": []},
           "promoted_recipe": None}
    return _finish(doc)


DRAFT_EDITABLE = ("name", "step", "status", "base_recipe", "selections", "pending_fields", "working_nodes",
                  "authoring_view")


def revised_draft(current: dict, changes: dict) -> dict:
    unknown = set(changes) - set(DRAFT_EDITABLE)
    if unknown:
        raise contracts.ContractViolation("cs-authoring-draft-v1", [(f"/{k}", "not an editable field")
                                                                    for k in sorted(unknown)])
    doc = {k: v for k, v in current.items() if k != "sha256"}
    doc.update(changes)
    doc.update({"revision": current["revision"] + 1, "updated_at": now_utc(), "sha256": "0" * 64})
    return _finish(doc)


def request_semantic(doc: dict) -> str:
    return request_semantic_sha256(doc)


def treatment_semantic(doc: dict) -> str:
    return treatment_semantic_sha256(doc)


def version_ref(doc: dict, kind: str, authority: str = "creative-studio") -> dict:
    return {"authority": authority, "workspace_id": doc["workspace_id"], "kind": kind, "id": doc["id"],
            "version_id": doc["version_id"], "revision": doc["revision"], "sha256": doc["sha256"]}
