#!/usr/bin/env python3
"""PromptCompiler v2 — assemble ProviderPrompt from structured specs only."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import compile_validator
import knowledge_loader
import spec_validator
import spec_validator_v2

_CONSTRAINT_MAP = {
    "geometry": "preserve_geometry",
    "pose": "preserve_pose",
    "material": "preserve_geometry",
    "band_placement": "preserve_pose",
    "lighting": None,
    "background": None,
}


class PromptCompilerV2Error(ValueError):
    """Raised when v2 compilation cannot produce a valid ProviderPrompt."""


def _canonical_json(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _content_hash(provider: dict) -> str:
    payload = copy.deepcopy(provider)
    payload.get("metadata", {}).pop("content_hash", None)
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _format_fragment(template: str, params: dict[str, Any]) -> str:
    out = template
    for key, val in params.items():
        out = out.replace("{" + key + "}", str(val))
    return out


def _scene_block(creative: dict[str, Any]) -> list[str]:
    scene = creative.get("scene") or {}
    parts: list[str] = []
    if scene.get("description"):
        parts.append(scene["description"])
    for key in ("lighting", "camera", "composition", "background"):
        if scene.get(key):
            parts.append(f"{key.replace('_', ' ').title()}: {scene[key]}")
    for lock in creative.get("scene_locks") or []:
        inv = lock.get("invariants") or {}
        for k, v in inv.items():
            parts.append(f"Lock {lock.get('id', k)}: {k}={v}")
    return parts


def _product_block(product: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    geom = (product.get("product") or {}).get("geometry") or {}
    if geom.get("description"):
        parts.append(f"Product geometry: {geom['description']}")
    materials = (product.get("product") or {}).get("materials") or []
    for mat in materials:
        label = mat.get("id", "material")
        desc = ", ".join(f"{k}={v}" for k, v in mat.items() if k != "id")
        parts.append(f"Material {label}: {desc}")
    constraints = product.get("constraints") or {}
    if constraints.get("preserve_geometry"):
        parts.append("Preserve exact product geometry and proportions.")
    if constraints.get("preserve_material"):
        parts.append("Preserve material and colour fidelity.")
    protected = (product.get("editing") or {}).get("protected_regions") or []
    if protected:
        parts.append("Protected regions: " + ", ".join(protected) + ".")
    return parts


def _operation_block(op: dict[str, Any]) -> str:
    op_id = op["op"]
    try:
        tmpl = knowledge_loader.load_operation(op_id)
    except knowledge_loader.KnowledgeError as exc:
        raise PromptCompilerV2Error(f"Unknown operation '{op_id}': {exc}") from exc
    fragment = tmpl.get("prompt_fragment", "")
    params = {k: v for k, v in op.items() if k != "op"}
    params.setdefault("scale", op.get("scale", 1.0))
    return _format_fragment(fragment, params)


def _constraint_fragments(preserve: list[str]) -> list[str]:
    parts: list[str] = []
    for item in preserve:
        cid = _CONSTRAINT_MAP.get(item)
        if not cid:
            parts.append(f"Preserve {item.replace('_', ' ')}.")
            continue
        try:
            frag = knowledge_loader.load_constraint(cid)
            if frag.get("prompt_fragment"):
                parts.append(frag["prompt_fragment"])
        except knowledge_loader.KnowledgeError:
            parts.append(f"Preserve {item.replace('_', ' ')}.")
    return parts


def compile_v2(
    product_spec: dict[str, Any],
    creative_spec: dict[str, Any],
    execution_request: dict[str, Any],
    *,
    edit_specs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Structured specs → ProviderPrompt v1 (semantic assembly, not string concat)."""
    lane = compile_validator.validate_for_compile(
        product_spec,
        creative_spec,
        execution_request,
        edit_specs=edit_specs,
    )
    if lane != "v2":
        raise PromptCompilerV2Error("compile_v2 requires product-spec-v2 and creative-spec-v2.")

    sections: list[str] = []
    sections.extend(_scene_block(creative_spec))
    sections.extend(_product_block(product_spec))

    edit_ids: list[str] = []
    forbidden: list[str] = []
    preserve: list[str] = []
    for edit in edit_specs or []:
        edit_ids.append(edit["id"])
        for op in edit.get("operations") or []:
            sections.append(_operation_block(op))
        cons = edit.get("constraints") or {}
        for p in cons.get("preserve") or []:
            if p not in preserve:
                preserve.append(p)
        for f in cons.get("forbidden") or []:
            if f not in forbidden:
                forbidden.append(f)

    sections.extend(_constraint_fragments(preserve))
    positive = " ".join(s.strip().rstrip(".") + "." for s in sections if s.strip())

    negative_parts = [f"No {f.replace('_', ' ')}." for f in forbidden]
    negative = " ".join(negative_parts) if negative_parts else None

    refs: list[str] = []
    for edit in edit_specs or []:
        for role in edit.get("reference_roles") or []:
            aid = role.get("asset_id")
            if aid and aid not in refs:
                refs.append(aid)
    exec_ref = execution_request.get("reference")
    if isinstance(exec_ref, str) and exec_ref not in refs:
        refs.insert(0, exec_ref)
    elif isinstance(exec_ref, dict) and exec_ref.get("image"):
        img = exec_ref["image"]
        if img not in refs:
            refs.insert(0, img)

    engine = execution_request.get("engine") or {}
    engine_params = engine.get("params") or {}
    input_images = list(engine_params.get("input_images") or [])

    provider: dict[str, Any] = {
        "schema_version": spec_validator.PROVIDER_SCHEMA,
        "positive": positive,
        "negative": negative,
        "metadata": {
            "execution_id": execution_request["id"],
            "product_spec_id": product_spec["id"],
            "creative_spec_id": creative_spec["id"],
            "edit_spec_ids": edit_ids,
            "compiler": "prompt-compiler-v2",
            "spec_lane": "v2",
            "placeholders_resolved": {},
        },
        "input_assets": {
            "references": refs,
            "input_images": input_images,
        },
    }
    provider["metadata"]["content_hash"] = _content_hash(provider)
    spec_validator.validate_provider_prompt(provider)
    return provider
