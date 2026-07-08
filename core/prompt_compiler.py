#!/usr/bin/env python3
"""PromptCompiler — triple-spec compile to ProviderPrompt v1 (deterministic)."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from typing import Any

import jobspec
import spec_compiler
import spec_validator

_PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")

# Human-readable per-operation instruction fragments, folded into the positive
# prompt ONLY when compile() is called with exactly one edit_spec (the shape
# job_compiler.compile_job_multi_op uses — one independent compile per op).
# The pre-existing merged-call shape (edit_specs=None or 2+ edit_specs, as used
# by compile_job) is intentionally left untouched to avoid changing its output.
_OP_INSTRUCTIONS: dict[str, str] = {
    "replace_logo": "Operation: replace the brand logo/mark region.",
    "replace_child": "Operation: replace the depicted child/model.",
    "remove_element": "Operation: remove the specified element.",
    "insert_mark": "Operation: insert the specified mark/logo.",
    "region_edit": "Operation: edit only the specified region.",
    "subject_swap": "Operation: swap the subject while preserving product placement.",
    "increase_spacing": "Operation: increase spacing as specified.",
    "reposition_logo": "Operation: reposition the logo/mark.",
    "remove_background": "Operation: remove the background.",
    "change_colour": "Operation: change the colour as specified.",
}


class PromptCompilerError(ValueError):
    """Raised when compilation cannot produce a valid ProviderPrompt."""


def _canonical_json(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _content_hash(provider: dict) -> str:
    payload = copy.deepcopy(provider)
    payload.get("metadata", {}).pop("content_hash", None)
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _resolve_references(reference: Any) -> list[str]:
    refs: list[str] = []
    if reference is None:
        return refs
    if isinstance(reference, str):
        return [reference]
    if isinstance(reference, dict):
        img = reference.get("image")
        return [img] if img else []
    if isinstance(reference, list):
        for item in reference:
            refs.extend(_resolve_references(item))
    return refs


def _load_cad_text(cad: str | None, job_dir: str) -> str:
    """Mirror run_job._load_cad — inline text or file path relative to job dir."""
    if not cad:
        return ""
    looks_like_path = (
        len(cad) < 400 and "\n" not in cad and (cad.endswith((".md", ".txt")) or "/" in cad)
    )
    path = cad if os.path.isabs(cad) else os.path.join(job_dir, cad)
    if os.path.exists(path) and looks_like_path:
        with open(path, encoding="utf-8") as f:
            return f.read()
    return cad


def compile(
    product_spec: dict[str, Any],
    creative_spec: dict[str, Any],
    execution_request: dict[str, Any],
    *,
    edit_specs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compile ProductSpec + CreativeSpec + EditSpec[] + ExecutionRequest → ProviderPrompt."""
    spec_validator.validate_product_spec(product_spec)
    spec_validator.validate_creative_spec(creative_spec)
    spec_validator.validate_execution_request(execution_request)
    for edit in edit_specs or []:
        spec_validator.validate_edit_spec(edit)

    out = execution_request.get("output") or {}
    ctx: dict[str, Any] = dict(creative_spec.get("default_subject") or {})
    ctx.update(dict(execution_request.get("subject") or {}))
    ctx.setdefault("composition", out.get("composition") or creative_spec.get("default_composition", ""))
    ctx.setdefault("background", out.get("background") or creative_spec.get("default_background", ""))
    ctx.setdefault("cad", product_spec.get("geometry_text", ""))

    tmpl = creative_spec["prompt_template"]
    prompt = tmpl
    for key, value in ctx.items():
        prompt = prompt.replace("{" + key + "}", str(value))

    tail: list[str] = []
    if out.get("composition") and "{composition}" not in tmpl:
        tail.append(out["composition"])
    if out.get("background") and "{background}" not in tmpl:
        tail.append("Background: " + out["background"])
    if edit_specs is not None and len(edit_specs) == 1:
        op_instruction = _OP_INSTRUCTIONS.get(edit_specs[0].get("operation"))
        if op_instruction:
            tail.append(op_instruction)
    if tail:
        prompt = prompt.rstrip(". ") + ". " + ". ".join(tail) + "."

    leftover = _PLACEHOLDER_RE.findall(prompt)
    if leftover:
        raise PromptCompilerError(
            f"Unresolved placeholder(s) {sorted(set(leftover))} in final prompt."
        )

    engine = execution_request.get("engine") or {}
    engine_params = engine.get("params") or {}
    input_images = list(engine_params.get("input_images") or [])

    placeholders_resolved = {k: str(v) for k, v in ctx.items()}

    negative = creative_spec.get("negative_template")
    edit_ids: list[str] = []
    exclusion_tail: list[str] = []
    for edit in edit_specs or []:
        edit_ids.append(edit["id"])
        for exc in edit.get("exclusions") or []:
            if exc and exc not in exclusion_tail:
                exclusion_tail.append(exc)
    if exclusion_tail:
        neg_parts = [negative] if negative else []
        neg_parts.extend(exclusion_tail)
        negative = ", ".join(p for p in neg_parts if p)

    provider: dict[str, Any] = {
        "schema_version": spec_validator.PROVIDER_SCHEMA,
        "positive": prompt,
        "negative": negative,
        "metadata": {
            "execution_id": execution_request["id"],
            "product_spec_id": product_spec["id"],
            "creative_spec_id": creative_spec["id"],
            "edit_spec_ids": edit_ids,
            "placeholders_resolved": placeholders_resolved,
            "composition": str(ctx.get("composition", "")),
            "background": str(ctx.get("background", "")),
        },
        "input_assets": {
            "references": _resolve_references(execution_request.get("reference")),
            "input_images": input_images,
        },
    }
    provider["metadata"]["content_hash"] = _content_hash(provider)
    spec_validator.validate_provider_prompt(provider)
    return provider


def compile_from_job_v1(job: dict[str, Any]) -> dict[str, Any]:
    """Lower job-v1 via job_compiler (full extract + compile + plan chain)."""
    import job_compiler

    artifacts = job_compiler.compile_job(job, dry_run=bool(job.get("dry_run", False)))
    return artifacts["provider_prompt"]


def compile_positive(job: dict[str, Any]) -> str:
    """Thin helper for run_job parity — returns positive prompt string only."""
    return compile_from_job_v1(job)["positive"]
