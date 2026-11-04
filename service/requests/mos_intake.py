"""MOS ContentPack visual request -> creative-request-v1 fields + draft treatment fields.

Input: ``mos-visual-request-v1`` (ENG-04c: one job = placement x canvas x slot x
tier, copy version ids, optional approved-draft reference) and the immutable
source versions MOS's own API exports for it (``mos-campaign-sources-v1``:
ContentPack, concept + approval, claims, offering, audiences, budget, ...).
Output is deterministic in those inputs, so re-running the intake for the same
MOS sources yields the same request and treatment semantic hashes.

Exact copy, logos and claims stay composition inputs; the generation direction
only asks for clear space for them (contract §6). Nothing is guessed: an
unsupported canvas, unsourced claim, missing budget or missing copy selection is
a typed refusal.
"""
from __future__ import annotations

from ..brand.errors import InputRefusal
from ..brand.mos_brandkit import slug

VISUAL_REQUEST_SCHEMA = "mos-visual-request-v1"
SOURCES_SCHEMA = "mos-campaign-sources-v1"

# ENG-04c device canvases -> contract export presets (common.schema.json#/$defs/format).
# 16:9 has no v1 preset and is refused rather than approximated.
_IMAGE_PRESETS = {"1:1": ("square_1080", 1080, 1080), "4:5": ("portrait_1080x1350", 1080, 1350),
                  "9:16": ("story_1080x1920", 1080, 1920)}
_VIDEO_PRESETS = {"9:16": ("video_story_1080x1920_30fps", 1080, 1920)}
_QUALITY = {"draft": "standard", "final": "high"}


def _require(cond, code: str, message: str, path: str):
    if not cond:
        raise InputRefusal(code, message, [path])


def _copy_entries(variant: dict, copy_ref: dict, locale: str, claims: dict) -> list[dict]:
    fields = variant.get("fields") or {}
    prefix = f"v{variant.get('variant_index', 1)}"
    claim_refs = []
    for c in fields.get("claim_refs") or []:
        ref = claims.get(c.get("source_id"))
        _require(ref is not None, "claim_unsourced", f"claim {c.get('claim')!r} has no pinned source in the claim set",
                 "/content_pack/content/copy_variants")
        claim_refs.append(ref)
    out = []

    def add(suffix: str, role: str, text):
        if isinstance(text, str) and text:
            out.append({"id": slug(f"{prefix}-{suffix}", "copy"), "role": role, "text": text, "language": locale,
                        "source_copy": copy_ref, "claim_refs": claim_refs})

    add("headline", "headline", fields.get("headline"))
    primary = fields.get("primary_text") or {}
    for tier in ("short", "medium", "long"):
        add(f"body-{tier}", "body", primary.get(tier))
    structure = fields.get("structure") or {}
    for item in structure.get("items") or []:
        add(f"item-{item.get('index')}", "other", item.get("text"))
    add("cta", "cta", fields.get("cta"))
    return out


def build_request_fields(visual_request: dict, sources: dict, brand_package: dict) -> dict:
    """creative-request-v1 editable fields for one MOS visual job."""
    _require(visual_request.get("schema_version") == VISUAL_REQUEST_SCHEMA, "unsupported_schema_version",
             f"expected {VISUAL_REQUEST_SCHEMA}", "/visual_request/schema_version")
    _require(sources.get("schema_version") == SOURCES_SCHEMA, "unsupported_schema_version",
             f"expected {SOURCES_SCHEMA}", "/sources/schema_version")
    mos_ws = sources["workspace_id"]
    _require(visual_request.get("workspace_id") == mos_ws, "scope_mismatch", "visual request is from another workspace",
             "/visual_request/workspace_id")
    pack = sources["content_pack"]["content"]
    placement = pack["placement"]["value"]
    _require(placement["placement_id"] == visual_request["placement_id"] == pack["pack_key"], "source_revision_changed",
             "ContentPack is for a different placement", "/content_pack/content/pack_key")
    _require(placement["concept_id"] == visual_request["concept_id"] == sources["concept"]["id"],
             "source_revision_changed", "concept differs from the placement's concept", "/concept")
    pins = pack["pins"]["value"]
    _require(pins["brand_kit"]["id"] == brand_package["source_artifact_id"], "approval_stale",
             "ContentPack pins a different BrandKit version than the imported package",
             "/content_pack/content/pins/value/brand_kit")
    locale = sources["locale"]

    # copy: the reviewer's selection, else every variant that passed MOS's automated checks
    copy_index = sources["copy_versions"]
    requested = {c["version_id"] for c in visual_request.get("copy_refs") or []}
    _require(requested, "copy_selection_required", "visual request names no copy versions", "/visual_request/copy_refs")
    missing = requested - set(copy_index)
    _require(not missing, "source_unavailable", f"copy versions not exported: {sorted(missing)}", "/copy_versions")
    variants = pack["copy_variants"]["value"]
    selected = (pack.get("selected_copy") or {}).get("value")
    if selected:
        chosen = [v for v in variants if v["copy_version_id"] == selected["copy_version_id"]]
    else:
        chosen = [v for v in variants if v.get("check_state") == "passed" and v["copy_version_id"] in requested]
    _require(chosen, "copy_selection_required", "no selected or check-passed copy variant for this placement",
             "/content_pack/content/copy_variants")
    exact_copy = []
    for v in sorted(chosen, key=lambda v: v.get("variant_index", 0)):
        exact_copy += _copy_entries(v, copy_index[v["copy_version_id"]], locale, sources.get("claims") or {})

    # formats: device canvas -> contract preset (one output per job)
    kind = visual_request.get("media_kind")
    canvas = visual_request.get("canvas")
    channel = ",".join(placement.get("channels") or [])[:80] or "unspecified"
    if kind == "image":
        _require(canvas in _IMAGE_PRESETS, "unsupported_canvas", f"canvas {canvas} has no v1 image preset",
                 "/visual_request/canvas")
        preset, w, h = _IMAGE_PRESETS[canvas]
        fmt = {"id": slug(f"{preset}-slot-{visual_request.get('slot')}"), "kind": "image", "preset": preset,
               "width_px": w, "height_px": h, "mime_type": "image/png", "duration_frames": None, "frame_rate": None,
               "count": 1, "channel": channel}
    else:
        _require(kind == "video" and canvas in _VIDEO_PRESETS, "unsupported_canvas",
                 f"{kind} canvas {canvas} has no v1 preset", "/visual_request/canvas")
        script = sources.get("production_script") or {}
        _require(isinstance(script.get("duration_frames"), int), "source_unavailable",
                 "video needs the approved production script duration", "/production_script")
        preset, w, h = _VIDEO_PRESETS[canvas]
        fmt = {"id": slug(f"{preset}-slot-{visual_request.get('slot')}"), "kind": "video", "preset": preset,
               "width_px": w, "height_px": h, "mime_type": "video/mp4", "duration_frames": script["duration_frames"],
               "frame_rate": {"num": 30, "den": 1}, "count": 1, "channel": channel}

    references = []
    ref = visual_request.get("reference")
    if visual_request.get("tier") == "final":
        _require(ref and ref.get("role") == "approved_draft", "source_unavailable",
                 "final tier needs the signed-off draft as reference", "/visual_request/reference")
    if ref:
        asset = (sources.get("references") or {}).get(ref["asset_version_id"])
        _require(asset is not None, "source_unavailable", "reference asset version not exported", "/references")
        references.append({"id": "approved-draft", "role": "composition", "asset": asset, "required": True,
                           "strength": None})

    budget = sources.get("budget")
    _require(isinstance(budget, dict), "budget_missing", "MOS exported no budget policy for this job", "/budget")
    selection = sources.get("provider_selection") or {
        "mode": "auto", "provider": None, "model_id": None, "quality": _QUALITY[visual_request["tier"]],
        "max_unit_cost_micros": budget["max_cost_micros"]}

    forbidden = list(dict.fromkeys(brand_package["document"]["imagery"]["prohibited"]
                                   + brand_package["document"]["voice"]["prohibited_terms"]))[:100]
    required = [r["instruction"] for r in brand_package["document"]["usage_rules"]
                if r["severity"] == "required"][:100]
    return {
        "origin": {"kind": "marketing-os", "mos_workspace_id": mos_ws,
                   "campaign_brief": sources["campaign_brief"], "concept": sources["concept"],
                   "concept_approval": sources["concept_approval"], "placement": sources["content_pack"]["ref"],
                   "copy_versions": [copy_index[c] for c in sorted(requested)],
                   "destination": sources["destination"], "claim_set": sources["claim_set"],
                   "policy": sources["policy"], "brand_rule_revision": None, "brand_rule_approval": None,
                   "generation_eligibility": None},   # filled by the caller from MOS's eligibility API
        "brand_package": brand_package["ref"],
        "offering": sources["offering"],
        "audiences": sources["audiences"],
        "objective": sources["objective"],
        "hook": placement["hook"],
        "locale": locale,
        "exact_copy": exact_copy[:50],
        "formats": [fmt],
        "references": references,
        "provider_selection": selection,
        "budget": budget,
        "constraints": {"forbidden": forbidden, "required": required, "exact_logo_layers": True,
                        "exact_copy_layers": True},
    }


def build_treatment_fields(sources: dict, request_ref: dict, brand_package_ref: dict) -> dict:
    """Draft treatment from the approved MOS concept's direction (no model call)."""
    direction = sources.get("concept_direction")
    _require(isinstance(direction, dict), "source_unavailable", "concept has no visual direction", "/concept_direction")
    return {
        "source_request": request_ref,
        "authorship": {"kind": "human", "model_id": None, "prompt_template": None, "raw_response_sha256": None,
                       "operation_id": None, "attempt_id": None},
        "direction": {"summary": direction["summary"], "scene": direction["scene"], "style": direction["style"],
                      "lighting": direction["lighting"], "camera": direction["camera"],
                      "composition": direction["composition"], "negative": list(direction.get("negative") or [])},
        "shots": [],
        "sources": [sources["concept"], sources["content_pack"]["ref"], brand_package_ref],
    }


# -- Marketing OS image library (LibraryPack visual_library item) ------------------------------------------------
LIBRARY_SOURCES_SCHEMA = "mos-library-sources-v1"


def is_library_request(visual_request: dict) -> bool:
    return isinstance(visual_request, dict) and visual_request.get("source") == "library"


def build_library_request_fields(visual_request: dict, sources: dict, brand_package: dict, credential_binding_id: str) -> dict:
    """creative-request-v1 fields for one approved image-library brief: no campaign concept, copy, claims or
    destination (origin kind ``marketing-os-library``, exact_copy empty). MOS supplies the brief's direction, the
    offer facts, the per-offer budget ceiling and the library approval; the funding binding is this connection."""
    _require(visual_request.get("schema_version") == VISUAL_REQUEST_SCHEMA, "unsupported_schema_version",
             f"expected {VISUAL_REQUEST_SCHEMA}", "/visual_request/schema_version")
    _require(sources.get("schema_version") == LIBRARY_SOURCES_SCHEMA, "unsupported_schema_version",
             f"expected {LIBRARY_SOURCES_SCHEMA}", "/sources/schema_version")
    mos_ws = sources["workspace_id"]
    _require(visual_request.get("workspace_id") == mos_ws, "scope_mismatch", "visual request is from another workspace",
             "/visual_request/workspace_id")
    item = sources.get("item") or {}
    asked = (visual_request.get("library") or {}).get("item_key")
    _require(item.get("key") == asked, "source_revision_changed", "library item differs from the request",
             "/sources/item/key")
    _require(item.get("production") == "generate", "source_unavailable",
             "this library brief is for real photographs and is never generated", "/sources/item/production")
    _require(sources["brand_kit"]["id"] == brand_package["source_artifact_id"], "approval_stale",
             "the library pins a different BrandKit version than the imported package", "/sources/brand_kit")
    _require(visual_request.get("media_kind") == "image" and visual_request.get("tier") == "draft", "unsupported_canvas",
             "library briefs are draft still images", "/visual_request/media_kind")
    canvas = visual_request.get("canvas")
    _require(canvas in _IMAGE_PRESETS and canvas == item.get("canvas"), "unsupported_canvas",
             f"canvas {canvas} has no v1 image preset or differs from the brief", "/visual_request/canvas")
    preset, w, h = _IMAGE_PRESETS[canvas]
    fmt = {"id": slug(f"{preset}-slot-{visual_request.get('slot')}"), "kind": "image", "preset": preset, "width_px": w,
           "height_px": h, "mime_type": "image/png", "duration_frames": None, "frame_rate": None, "count": 1,
           "channel": "library"}
    budget = sources.get("budget")
    _require(isinstance(budget, dict), "budget_missing", "MOS exported no library budget for this brief", "/budget")
    budget = {**budget, "credential_binding_id": credential_binding_id}
    selection = sources.get("provider_selection") or {
        "mode": "auto", "provider": None, "model_id": None, "quality": _QUALITY["draft"],
        "max_unit_cost_micros": budget["max_cost_micros"]}
    forbidden = list(dict.fromkeys(brand_package["document"]["imagery"]["prohibited"]
                                   + brand_package["document"]["voice"]["prohibited_terms"]
                                   + list(sources.get("forbidden") or [])))[:100]
    required = [r["instruction"] for r in brand_package["document"]["usage_rules"] if r["severity"] == "required"][:100]
    return {
        "origin": {"kind": "marketing-os-library", "mos_workspace_id": mos_ws, "library": sources["library"],
                   "library_item_key": item["key"], "library_approval": sources["library_approval"],
                   "policy": sources["policy"], "brand_rule_revision": None, "brand_rule_approval": None,
                   "generation_eligibility": None},   # filled by the caller from MOS's eligibility API
        "brand_package": brand_package["ref"],
        "offering": sources["offering"],
        "audiences": sources["audiences"],
        "objective": sources["objective"],
        "hook": sources["hook"],
        "locale": sources["locale"],
        "exact_copy": [],
        "formats": [fmt],
        "references": [],
        "provider_selection": selection,
        "budget": budget,
        "constraints": {"forbidden": forbidden, "required": required, "exact_logo_layers": True, "exact_copy_layers": True},
    }


def build_library_treatment_fields(sources: dict, request_ref: dict, brand_package_ref: dict) -> dict:
    """Draft treatment from the approved library brief's own direction (no model call)."""
    direction = sources.get("concept_direction")
    _require(isinstance(direction, dict), "source_unavailable", "library brief has no visual direction", "/concept_direction")
    return {
        "source_request": request_ref,
        "authorship": {"kind": "human", "model_id": None, "prompt_template": None, "raw_response_sha256": None,
                       "operation_id": None, "attempt_id": None},
        "direction": {"summary": direction["summary"], "scene": direction["scene"], "style": direction["style"],
                      "lighting": direction["lighting"], "camera": direction["camera"],
                      "composition": direction["composition"], "negative": list(direction.get("negative") or [])},
        "shots": [],
        "sources": [sources["library"], brand_package_ref],
    }
