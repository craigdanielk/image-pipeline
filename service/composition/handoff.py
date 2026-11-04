"""cs-mos-handoff-v1 payloads and ingestion-receipt verification (contract.md §11).

The handoff offers exact export versions; it transfers no approval. Every
exported copy string must be bound to a MOS copy version listed in the target
(otherwise `copy_revision_mismatch`). Costs are combined once per plan so
shared attempts are not double counted.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .canonical import artifact_sha256
from .contracts import require_valid
from .errors import CompositionError
from .export import READABLE_EXPORT_SCHEMA_VERSIONS, version_ref

_STATE_RANK = {"settled": 0, "reserved": 1, "unknown": 2}


def _ref_key(ref: dict) -> tuple:
    return (ref["authority"], ref["workspace_id"], ref["id"], ref["version_id"], ref["sha256"])


def export_ref(export: dict) -> dict:
    return version_ref(export, authority="creative-studio", kind="export")


def combine_cost(exports: list[dict]) -> dict:
    by_plan: dict[str, dict] = {}
    for e in exports:
        plan = e["lineage"]["plan"]["version_id"]
        if plan in by_plan and by_plan[plan] != e["cost"]:
            raise CompositionError("cost_lineage_conflict", f"exports of plan {plan} disagree on cost",
                                   ["/exports"], [e["id"]])
        by_plan[plan] = e["cost"]
    costs = list(by_plan.values())
    receipts = sorted({r for c in costs for r in c["receipt_ids"]})
    if len(receipts) > 1000:
        raise CompositionError("handoff_too_large", "combined cost receipts exceed 1000", ["/cost/receipt_ids"])
    return {
        "currency": "USD",
        "state": max((c["state"] for c in costs), key=_STATE_RANK.__getitem__),
        "estimated_micros": sum(c["estimated_micros"] for c in costs),
        "reserved_micros": sum(c["reserved_micros"] for c in costs),
        "settled_micros": sum(c["settled_micros"] for c in costs),
        "unresolved_micros": sum(c["unresolved_micros"] for c in costs),
        "receipt_ids": receipts,
    }


def build_handoff(exports: list[dict], *, target: dict, creative_request: dict, idempotency_key: str,
                  created_by: str, handoff_id: str | None = None, created_at: str | None = None) -> dict:
    if not exports:
        raise CompositionError("invalid_mos_handoff", "a handoff needs at least one export", ["/exports"])
    workspace_id = exports[0]["workspace_id"]
    package = exports[0]["lineage"]["brand_package"]
    for i, e in enumerate(exports):
        version = e.get("schema_version") if isinstance(e, dict) else None
        if version not in READABLE_EXPORT_SCHEMA_VERSIONS:
            raise CompositionError("unsupported_schema_version", f"export schema_version {version!r} is not one of "
                                   f"{', '.join(READABLE_EXPORT_SCHEMA_VERSIONS)}", [f"/exports/{i}/schema_version"])
        require_valid(e, version)
        if e["sha256"] != artifact_sha256(e):
            raise CompositionError("artifact_hash_mismatch", "export manifest hash does not match its content",
                                   [f"/exports/{i}"], [e["version_id"]])
        if e["workspace_id"] != workspace_id:
            raise CompositionError("scope_mismatch", "exports belong to different Studio workspaces",
                                   [f"/exports/{i}"])
        if _ref_key(e["lineage"]["brand_package"]) != _ref_key(package):
            raise CompositionError("source_revision_changed", "exports use different brand package versions",
                                   [f"/exports/{i}/lineage/brand_package"], [e["id"]])
        if _ref_key(e["lineage"]["request"]) != _ref_key(creative_request):
            raise CompositionError("source_revision_changed", "export was made from another creative request "
                                   "version", [f"/exports/{i}/lineage/request"], [e["id"]])
    # Library handoffs (target.library_item_key) carry no campaign copy: any copy layer is refused below.
    allowed_copy = {_ref_key(r) for r in target.get("copy_versions") or []}
    for i, e in enumerate(exports):
        for j, c in enumerate(e["exact_copy"]):
            src = c["source_copy"]
            if src is None or src["authority"] != "marketing-os" or _ref_key(src) not in allowed_copy:
                raise CompositionError("copy_revision_mismatch",
                                       f"copy {c['copy_id']} is not bound to a MOS copy version in this handoff; "
                                       "create the MOS copy revision first", [f"/exports/{i}/exact_copy/{j}"],
                                       [c["copy_id"]])
    refs = [export_ref(e) for e in exports]
    if len({_ref_key(r) for r in refs}) != len(refs):
        raise CompositionError("invalid_mos_handoff", "duplicate export in handoff", ["/exports"])
    handoff_id = handoff_id or str(uuid.uuid5(uuid.NAMESPACE_URL, f"cs-handoff:{workspace_id}:{idempotency_key}"))
    doc = {
        "schema_version": "cs-mos-handoff-v1",
        "id": handoff_id,
        "version_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"cs-handoff-version:{handoff_id}")),
        "revision": 1,
        "workspace_id": workspace_id,
        "created_at": created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_by": created_by,
        "sha256": "",
        "idempotency_key": idempotency_key,
        "target": target,
        "exports": refs,
        "brand_package": package,
        "creative_request": creative_request,
        "cost": combine_cost(exports),
        "requires_mos_review": True,
    }
    doc["sha256"] = artifact_sha256(doc)
    require_valid(doc, "cs-mos-handoff-v1")
    return doc


def verify_ingestion_receipt(receipt: dict, handoff: dict, exports: list[dict]) -> None:
    """Accept a MOS receipt only if it binds exactly the offered bytes and versions."""
    require_valid(receipt, "cs-mos-ingestion-receipt-v1")
    if receipt["sha256"] != artifact_sha256(receipt):
        raise CompositionError("artifact_hash_mismatch", "receipt hash does not match its content", ["/sha256"])
    if (receipt["handoff_id"], receipt["handoff_sha256"], receipt["idempotency_key"]) != \
            (handoff["id"], handoff["sha256"], handoff["idempotency_key"]):
        raise CompositionError("receipt_mismatch", "receipt is for another handoff", ["/handoff_id"])
    if receipt["workspace_id"] != handoff["target"]["workspace_id"]:
        raise CompositionError("scope_mismatch", "receipt is from another MOS workspace", ["/workspace_id"])
    by_ref = {_ref_key(export_ref(e)): e for e in exports}
    offered = {_ref_key(r) for r in handoff["exports"]}
    got = set()
    for i, item in enumerate(receipt["items"]):
        key = _ref_key(item["export"])
        export = by_ref.get(key)
        if key not in offered or export is None:
            raise CompositionError("receipt_mismatch", "receipt item names an export not in the handoff",
                                   [f"/items/{i}/export"])
        file_ref = export["file"]["ref"]
        if item["export_asset"] != file_ref or item["stored_bytes_sha256"] != file_ref["sha256"]:
            raise CompositionError("asset_hash_mismatch", "MOS stored bytes differ from the exported file",
                                   [f"/items/{i}/stored_bytes_sha256"], [export["id"]])
        got.add(key)
    if got != offered:
        raise CompositionError("receipt_incomplete", "receipt does not cover every offered export; partial "
                               "transfer is not a complete receipt", ["/items"])
