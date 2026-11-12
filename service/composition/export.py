"""cs-export-v2 manifests (contract.md §10/§11, export-v2.schema.json).

A manifest is issued only after the rendered bytes are persisted through an
ExportStore and read back: byte hash, MIME, dimensions re-derived from the
stored bytes must equal the render. Rights must permit embedding for the
format's channel; exact copy is bound by copy_id + text_sha256; unknown
generation cost is carried as-is (never rewritten to zero).
"""
from __future__ import annotations

import hashlib
import io
import uuid
from datetime import datetime, timezone
from typing import Protocol

from PIL import Image

from .canonical import artifact_sha256
from .contracts import require_valid
from .errors import CompositionError
from .renderer import RenderResult

# Contract image presets (common.schema.json#/$defs/export_format_v2, contract.md §10):
# the operator-ratified E5 canvases 1:1 / 4:5 / 9:16 / 16:9. Any other artboard size
# renders but is refused at export.
EXPORT_SCHEMA_VERSION = "cs-export-v2"
READABLE_EXPORT_SCHEMA_VERSIONS = ("cs-export-v1", "cs-export-v2")
IMAGE_PRESETS = {
    "square_1080": (1080, 1080),
    "portrait_1080x1350": (1080, 1350),
    "story_1080x1920": (1080, 1920),
    "landscape_1920x1080": (1920, 1080),
}
_DECODED_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


class ExportStore(Protocol):
    def persist(self, *, workspace_id: str, data: bytes, mime_type: str, name: str, width: int, height: int,
                rights: dict, parent_assets: list[dict]) -> dict:
        """Durably store immutable bytes; return the committed common asset_record."""

    def read(self, ref: dict) -> bytes | None:
        """Read back stored bytes for an exact AssetRef."""


class MemoryExportStore:
    """Test/in-process store with immutable versions (no overwrite)."""

    def __init__(self) -> None:
        self.blobs: dict[tuple[str, str], bytes] = {}
        self.records: dict[tuple[str, str], dict] = {}

    def persist(self, *, workspace_id, data, mime_type, name, width, height, rights, parent_assets):
        digest = hashlib.sha256(data).hexdigest()
        asset_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"cs-export-asset:{workspace_id}:{digest}"))
        ref = {"authority": "creative-studio", "workspace_id": workspace_id, "asset_id": asset_id,
               "asset_version_id": asset_id, "revision": 1, "sha256": digest, "mime_type": mime_type}
        key = (workspace_id, ref["asset_version_id"])
        if key in self.blobs and self.blobs[key] != data:
            raise CompositionError("asset_hash_mismatch", "immutable asset version already holds other bytes")
        self.blobs[key] = data
        record = {"ref": ref, "name": name, "size_bytes": len(data), "width_px": width, "height_px": height,
                  "duration_ms": None, "rights": rights, "origin": "derived", "source_url": None,
                  "captured_at": None, "parent_assets": parent_assets}
        self.records[key] = record
        return record

    def read(self, ref):
        return self.blobs.get((ref["workspace_id"], ref["asset_version_id"]))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def version_ref(doc: dict, *, authority: str, kind: str) -> dict:
    return {"authority": authority, "workspace_id": doc["workspace_id"], "kind": kind, "id": doc["id"],
            "version_id": doc["version_id"], "revision": doc["revision"], "sha256": doc["sha256"]}


def check_format(fmt: dict, render: RenderResult) -> None:
    preset = IMAGE_PRESETS.get(fmt.get("preset"))
    if fmt.get("kind") != "image" or preset is None:
        raise CompositionError("unsupported_export_preset", f"{fmt.get('preset')!r} is not an image export preset",
                               ["/format/preset"])
    if (render.width, render.height) not in IMAGE_PRESETS.values():
        raise CompositionError("unsupported_export_preset",
                               f"artboard {render.width}x{render.height} has no export preset in {EXPORT_SCHEMA_VERSION} "
                               f"(supported: {', '.join(f'{w}x{h}' for w, h in IMAGE_PRESETS.values())})",
                               ["/format/preset"])
    if preset != (render.width, render.height) or (fmt["width_px"], fmt["height_px"]) != preset:
        raise CompositionError("format_mismatch", f"preset {fmt['preset']} is {preset[0]}x{preset[1]}; artboard "
                               f"rendered {render.width}x{render.height}", ["/format"])
    if fmt["mime_type"] != render.mime_type:
        raise CompositionError("format_mismatch", f"format wants {fmt['mime_type']}, render is {render.mime_type}",
                               ["/format/mime_type"])


LIBRARY_CHANNEL = "library"   # internal image-library destination (Marketing OS LibraryPack), not a publication
PLATES_CHANNEL = "plates"     # internal video-plate destination (Marketing OS plate set), not a publication

def _rights_check(record: dict, use: str, channel: str, at: datetime, role: str) -> dict:
    rights = record["rights"]
    rid = record["ref"]["asset_version_id"]
    if rights["status"] not in ("permitted", "restricted"):
        raise CompositionError("rights_denied", f"{role} rights are {rights['status']}; unknown is not permission",
                               ["/rights"], [rid])
    if use not in rights["allowed_uses"]:
        raise CompositionError("rights_denied", f"{role} rights do not allow '{use}'", ["/rights/allowed_uses"], [rid])
    # An empty channel list on permitted rights is "no channel restriction recorded" (the owner confirmed the brand
    # files as theirs without listing channels), not "no channel": it blocked Regenic's own logo on its own pages.
    if channel is not None and rights["channels"] and channel not in rights["channels"] \
            and "*" not in rights["channels"]:
        raise CompositionError("rights_denied", f"{role} rights do not cover channel {channel!r}",
                               ["/rights/channels"], [rid])
    if _parse_ts(rights["valid_from"]) > at or (rights["expires_at"] and _parse_ts(rights["expires_at"]) <= at):
        raise CompositionError("rights_denied", f"{role} rights are not valid now", ["/rights/expires_at"], [rid])
    return rights


def _derived_rights(source_rights: list[dict], *, receipt_id: str, recorded_by: str, at: str) -> dict:
    def meet(lists):
        concrete = [set(l) for l in lists if l and "*" not in l]
        if not concrete:
            return ["*"]
        out = set.intersection(*concrete)
        return sorted(out)
    bases = {r["basis"] for r in source_rights}
    expiries = sorted(r["expires_at"] for r in source_rights if r["expires_at"])
    restrictions = sorted({x for r in source_rights for x in r["restrictions"]})
    attributions = [r["attribution"] for r in source_rights if r["attribution"]]
    receipt = {
        "receipt_id": receipt_id, "revision": 1, "sha256": "",
        "status": "restricted" if any(r["status"] == "restricted" for r in source_rights) else "permitted",
        "holder": None, "basis": bases.pop() if len(bases) == 1 else "permission", "evidence": [],
        "allowed_uses": sorted(set.intersection(*[set(r["allowed_uses"]) for r in source_rights])),
        "channels": meet([r["channels"] for r in source_rights]),
        "territories": meet([r["territories"] for r in source_rights]),
        "valid_from": at, "expires_at": expiries[0] if expiries else None,
        "attribution": "; ".join(sorted(set(attributions)))[:2000] if attributions else None,
        "restrictions": (restrictions + ["derived from source rights receipts: " +
                                         ", ".join(sorted(r["receipt_id"] for r in source_rights))])[:40],
        "recorded_by": recorded_by,
    }
    receipt["sha256"] = artifact_sha256(receipt)
    return receipt


def build_export(render: RenderResult, *, composition: dict, brand_package: dict, fmt: dict, store: ExportStore,
                 source_records: list[dict], generated_sources: list[dict], lineage: dict,
                 copy_sources: dict[str, dict | None], cost: dict, created_by: str,
                 export_id: str | None = None, created_at: str | None = None, copy_required: bool = True) -> dict:
    """lineage: request, treatment, recipe, plan, catalogue (VersionRefs) and compile_hash."""
    check_format(fmt, render)
    receipt = render.receipt
    if (receipt["composition"]["version_id"], receipt["composition"]["sha256"]) != \
            (composition["version_id"], composition["sha256"]):
        raise CompositionError("source_revision_changed", "render was produced from another composition version",
                               ["/lineage/composition"], [composition["version_id"]])
    if (receipt["brand_package"]["version_id"], receipt["brand_package"]["sha256"]) != \
            (brand_package["version_id"], brand_package["sha256"]):
        raise CompositionError("source_revision_changed", "render used another brand package version",
                               ["/lineage/brand_package"], [brand_package["version_id"]])
    workspace_id = composition["workspace_id"]
    created_at = created_at or _now()
    at = _parse_ts(created_at)
    by_version = {r["ref"]["asset_version_id"]: r for r in source_records}
    generated = {s["asset"]["asset_version_id"]: s for s in generated_sources}

    # Every pixel source needs a rights record that allows embedding on this channel.
    used: list[tuple[str, dict]] = [(s["role"], s["asset"]) for s in receipt["sources"]]
    used += [("font", f["asset"]) for f in receipt["fonts"]]
    rights, other_assets, seen = [], [], set()
    for role, ref in used:
        record = by_version.get(ref["asset_version_id"])
        if record is None or record["ref"]["sha256"] != ref["sha256"]:
            raise CompositionError("source_lineage_missing", f"no asset record with rights for {role} "
                                   f"{ref['asset_version_id']}", ["/lineage"], [ref["asset_version_id"]])
        if ref["asset_version_id"] in seen:
            continue
        seen.add(ref["asset_version_id"])
        # An image-library export is stored for later use, not published: status, 'embed' use and validity still apply
        # here; the channel is checked when a library image is placed on a channel (MOS placement gate / export).
        scope = None if fmt["channel"] in (LIBRARY_CHANNEL, PLATES_CHANNEL) else fmt["channel"]
        rights.append(_rights_check(record, "embed", scope, at, role))
        if role == "image" and ref["asset_version_id"] not in generated and record["origin"] == "provider":
            raise CompositionError("source_lineage_missing", "provider image has no source_output attempt lineage",
                                   ["/lineage/generated_sources"], [ref["asset_version_id"]])
        if ref["asset_version_id"] not in generated:
            other_assets.append(ref)

    copy, by_copy = [], {}
    for entry in receipt["exact_copy"]:
        if entry["copy_id"] is None:
            raise CompositionError("copy_id_missing", f"text layer {entry['layer_id']} has no copy_id; exported copy "
                                   "must be bound to a copy entry", ["/exact_copy"], [entry["layer_id"]])
        prior = by_copy.get(entry["copy_id"])
        if prior is not None:
            if prior["text_sha256"] != entry["text_sha256"]:
                raise CompositionError("copy_revision_mismatch", f"copy_id {entry['copy_id']} has two different texts",
                                       ["/exact_copy"], [entry["copy_id"]])
            continue
        source_copy = copy_sources.get(entry["copy_id"])
        item = {"copy_id": entry["copy_id"], "text": entry["text"], "text_sha256": entry["text_sha256"],
                "source_copy": source_copy}
        by_copy[entry["copy_id"]] = item
        copy.append(item)
    if not copy and copy_required:   # image-library exports (copy_required=False) are images without campaign copy
        raise CompositionError("exact_copy_missing", "an export carries at least one exact copy entry",
                               ["/exact_copy"])

    export_id = export_id or str(uuid.uuid5(uuid.NAMESPACE_URL, f"cs-export:{composition['version_id']}:"
                                                                 f"{receipt['artboard']['id']}:{render.sha256}"))
    file_rights = _derived_rights(rights, receipt_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"rights:{export_id}")),
                                  recorded_by=created_by, at=created_at)
    record = store.persist(workspace_id=workspace_id, data=render.data, mime_type=render.mime_type,
                           name=f"{receipt['artboard']['id']}.{_EXT[render.mime_type]}", width=render.width,
                           height=render.height, rights=file_rights,
                           parent_assets=[ref for _, ref in used if ref["authority"] == "creative-studio"][:100])
    # Read-back: identity comes from the stored bytes, not from the render in memory.
    stored = store.read(record["ref"])
    stored_sha = hashlib.sha256(stored).hexdigest() if stored is not None else None
    if stored_sha != render.sha256 or stored_sha != record["ref"]["sha256"] or len(stored) != record["size_bytes"]:
        raise CompositionError("export_readback_mismatch", "stored export bytes/record do not match the render",
                               ["/file"], [record["ref"]["asset_version_id"]], retryable=True)
    with Image.open(io.BytesIO(stored)) as img:
        if _DECODED_MIME.get(img.format) != render.mime_type or img.size != (render.width, render.height) or \
                (record["width_px"], record["height_px"]) != img.size:
            raise CompositionError("export_readback_mismatch", "stored export MIME/dimensions differ from the render",
                                   ["/file"], [record["ref"]["asset_version_id"]], retryable=True)

    doc = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "id": export_id,
        "version_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"cs-export-version:{export_id}:{render.sha256}")),
        "revision": 1,
        "workspace_id": workspace_id,
        "created_at": created_at,
        "created_by": created_by,
        "sha256": "",
        "status": "complete",
        "format": fmt,
        "file": record,
        "thumbnail": None,
        "lineage": {
            "request": lineage["request"], "treatment": lineage["treatment"], "recipe": lineage["recipe"],
            "composition": version_ref(composition, authority="creative-studio", kind="composition"),
            "brand_package": version_ref(brand_package, authority="brand-ci-studio", kind="brand_package"),
            "plan": lineage["plan"], "compile_hash": lineage["compile_hash"], "catalogue": lineage["catalogue"],
            "generated_sources": [generated[k] for k in sorted(generated) if k in seen],
            "other_assets": other_assets,
            "render_engine_version": render.renderer_version,
            "font_asset_sha256s": sorted(f["sha256"] for f in receipt["fonts"]),
        },
        "exact_copy": copy,
        "cost": cost,
        "rights_summary": {
            "status": file_rights["status"],
            "receipt_ids": sorted({r["receipt_id"] for r in rights}),
            "restrictions": sorted({x for r in rights for x in r["restrictions"]})[:100],
            "attribution_text": file_rights["attribution"],
        },
        "requires_downstream_review": True,
    }
    doc["sha256"] = artifact_sha256(doc)
    require_valid(doc, EXPORT_SCHEMA_VERSION)
    return doc
