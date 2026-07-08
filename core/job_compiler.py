#!/usr/bin/env python3
"""Job compiler — extract, compile, and plan. No provider I/O.

The runtime never compiles. This module is the sole compile+plan entry for job-v1
source files. Output is a compile-artifacts bundle consumed by run_graph / run_job.

  cad.md → ProductSpec (extractor)
  job-v1 → CreativeSpec + EditSpec[] + ExecutionRequest
         → ProviderPrompt (PromptCompiler)
         → ProviderExecutionPlan (CapabilityResolver)
"""
from __future__ import annotations

import os
import re
from typing import Any

import capability_resolver
import jobspec
import prompt_compiler
import spec_compiler
import spec_extractor
import spec_validator

ARTIFACTS_SCHEMA = "compile-artifacts-v1"

_SURGICAL_RE = re.compile(r"ONLY\s+CHANGE(?:S)?", re.IGNORECASE)
_CAD_PATH_RE = re.compile(r"\.(md|txt)$|/")


class JobCompilerError(ValueError):
    """Raised when job-v1 cannot be compiled to artifacts."""


def _resolve_cad_path(cad_ref: str, job_dir: str) -> str:
    return cad_ref if os.path.isabs(cad_ref) else os.path.join(job_dir, cad_ref)


def _looks_like_cad_file(cad_ref: str) -> bool:
    return bool(_CAD_PATH_RE.search(cad_ref)) and "\n" not in cad_ref and len(cad_ref) < 400


def extract_product_spec_from_job(job: dict[str, Any]) -> dict[str, Any]:
    """cad.md → ProductSpec via extractor; inline cad → synthetic ProductSpec."""
    job_dir = job.get("_dir", ".")
    spec = job.get("spec") or {}
    cad_ref = spec.get("cad")

    if not cad_ref:
        product: dict[str, Any] = {
            "schema_version": spec_validator.PRODUCT_SCHEMA,
            "id": job["id"] + "-product",
            "name": job["id"],
            "immutable": True,
            "geometry_text": "(no product geometry specified)",
        }
        spec_validator.validate_product_spec(product)
        return product

    if _looks_like_cad_file(cad_ref):
        cad_path = _resolve_cad_path(cad_ref, job_dir)
        if os.path.exists(cad_path):
            product = spec_extractor.extract_product_spec(cad_path)
            with open(cad_path, encoding="utf-8") as f:
                product["geometry_text"] = f.read()
            product["id"] = job["id"] + "-product"
            spec_validator.validate_product_spec(product)
            return product

    product = {
        "schema_version": spec_validator.PRODUCT_SCHEMA,
        "id": job["id"] + "-product",
        "name": job["id"],
        "immutable": True,
        "geometry_text": cad_ref,
    }
    spec_validator.validate_product_spec(product)
    return product


def _extract_surgical_block(template: str) -> str:
    match = _SURGICAL_RE.search(template)
    if not match:
        return ""
    return template[match.start() :].strip()


def _infer_operation(template: str) -> str:
    lower = template.lower()
    if "replace" in lower and ("child" in lower or "kid" in lower or "model" in lower):
        return "subject_swap"
    if any(k in lower for k in ("logo", "mark", "branding", "crest")):
        return "replace_mark"
    if "remove" in lower:
        return "remove_element"
    return "lock_and_rerender"


def derive_edit_specs_from_job(
    job: dict[str, Any],
    *,
    creative_spec_id: str,
) -> list[dict[str, Any]]:
    """Lower surgical job-v1 prose into structured EditSpec v1 objects."""
    spec = job.get("spec") or {}
    template = spec.get("prompt_template") or ""
    negative = spec.get("negative") or spec.get("negative_template")

    has_surgical = bool(_SURGICAL_RE.search(template))
    has_parent = bool(job.get("parent_run_id"))
    ref = job.get("reference")

    if not has_surgical and not has_parent:
        return []

    parent_asset_id = job["id"] + "-anchor"
    if isinstance(ref, list) and ref:
        parent_asset_id = "asset:path:" + str(ref[0])
    elif isinstance(ref, str) and ref:
        parent_asset_id = "asset:path:" + ref
    elif isinstance(ref, dict) and ref.get("image"):
        parent_asset_id = "asset:path:" + str(ref["image"])

    exclusions: list[str] = []
    if negative:
        for part in re.split(r"[,;\n]+", negative):
            cleaned = part.strip()
            if cleaned:
                exclusions.append(cleaned)

    surgical = _extract_surgical_block(template)
    constraints: list[dict[str, str]] = []
    if surgical:
        constraints.append({"rule": "surgical_block", "value": surgical})

    edit: dict[str, Any] = {
        "schema_version": spec_validator.EDIT_SCHEMA,
        "id": job["id"] + "-edit",
        "creative_spec_id": creative_spec_id,
        "operation": _infer_operation(template),
        "parent_asset_id": parent_asset_id,
        "parent_run_id": job.get("parent_run_id"),
        "constraints": constraints,
        "exclusions": exclusions,
        "provenance": {
            "compiled_from": "job-v1",
            "source_job_path": job.get("_source_path") or job.get("_dir"),
        },
    }
    spec_validator.validate_edit_spec(edit)
    return [edit]


def _persist_context(job: dict[str, Any]) -> dict[str, Any]:
    """Minimal job-shaped dict for output persistence only."""
    out = jobspec.output(job)
    return {
        "id": job["id"],
        "_dir": job.get("_dir", "."),
        "output": out,
        "engine": job.get("engine") or {},
    }


def compile_job(job: dict[str, Any], *, dry_run: bool = True) -> dict[str, Any]:
    """Full compile chain from job-v1. Returns compile-artifacts-v1 bundle."""
    jobspec.validate(job)

    product = extract_product_spec_from_job(job)
    creative = spec_compiler.derive_creative_from_job(
        job, product_spec_id=product["id"]
    )
    edit_specs = derive_edit_specs_from_job(job, creative_spec_id=creative["id"])
    execution = spec_compiler.compile_execution_request(
        job, source_path=job.get("_source_path")
    )
    execution["dry_run"] = dry_run

    provider_prompt = prompt_compiler.compile(
        product, creative, execution, edit_specs=edit_specs
    )
    execution_plan = capability_resolver.resolve(
        provider_prompt, execution, job_dir=job.get("_dir", ".")
    )

    bundle: dict[str, Any] = {
        "schema_version": ARTIFACTS_SCHEMA,
        "job_id": job["id"],
        "job_dir": job.get("_dir", "."),
        "product_spec": product,
        "creative_spec": creative,
        "edit_specs": edit_specs,
        "execution_request": execution,
        "provider_prompt": provider_prompt,
        "execution_plan": execution_plan,
        "persist_context": _persist_context(job),
        "compile_hash": provider_prompt.get("metadata", {}).get("content_hash"),
    }
    bundle["graph"] = __import__("graph_compiler").compile_execution_graph(bundle)
    return bundle


def compile_job_multi_op(
    job: dict[str, Any],
    edit_specs: list[dict[str, Any]],
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Compile job-v1 + an explicit ordered EditSpec[] into a bundle carrying
    N distinct per-op execution plans (op_execution_plans), one per edit_spec,
    for graph_compiler to lower into a real chained multi-node graph.

    Unlike compile_job (which folds all edit_specs into ONE merged prompt),
    each edit_spec here gets its own independent prompt_compiler.compile() +
    capability_resolver.resolve() call, so each resulting node in the graph
    can be re-run in isolation with its own distinct compiled prompt.
    """
    jobspec.validate(job)

    product = extract_product_spec_from_job(job)
    creative = spec_compiler.derive_creative_from_job(job, product_spec_id=product["id"])
    execution = spec_compiler.compile_execution_request(job, source_path=job.get("_source_path"))
    execution["dry_run"] = dry_run
    job_dir = job.get("_dir", ".")

    op_execution_plans: list[dict[str, Any]] = []
    for edit in edit_specs:
        spec_validator.validate_edit_spec(edit)
        provider_prompt = prompt_compiler.compile(
            product, creative, execution, edit_specs=[edit], fold_operation_into_prompt=True
        )
        execution_plan = capability_resolver.resolve(provider_prompt, execution, job_dir=job_dir)
        op_execution_plans.append({
            "execution_plan": execution_plan,
            "job_dir": job_dir,
            "op": edit.get("operation", edit["id"]),
        })

    # Base bundle mirrors compile_job's non-graph fields; the LAST edit_spec's
    # prompt/plan (or a plain execution-only compile when there are no ops)
    # feeds the legacy single provider_prompt/execution_plan fields so any
    # code still reading bundle["execution_plan"] directly keeps working.
    if edit_specs:
        base_provider_prompt = prompt_compiler.compile(product, creative, execution, edit_specs=edit_specs)
    else:
        base_provider_prompt = prompt_compiler.compile(product, creative, execution, edit_specs=None)
    base_execution_plan = capability_resolver.resolve(base_provider_prompt, execution, job_dir=job_dir)

    bundle: dict[str, Any] = {
        "schema_version": ARTIFACTS_SCHEMA,
        "job_id": job["id"],
        "job_dir": job_dir,
        "product_spec": product,
        "creative_spec": creative,
        "edit_specs": edit_specs,
        "execution_request": execution,
        "provider_prompt": base_provider_prompt,
        "execution_plan": base_execution_plan,
        "persist_context": _persist_context(job),
        "compile_hash": base_provider_prompt.get("metadata", {}).get("content_hash"),
        "op_execution_plans": op_execution_plans,
    }
    bundle["graph"] = __import__("graph_compiler").compile_execution_graph(bundle)
    return bundle


def compile_path_to_dir(
    path: str,
    compile_dir: str,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Compile job-v1 and write compile/ directory. Returns manifest."""
    import compile_bundle

    job = jobspec.load(path)
    job["_source_path"] = os.path.abspath(path)
    bundle = compile_job(job, dry_run=dry_run)
    return compile_bundle.write_compile_dir(
        compile_dir,
        bundle,
        source={"kind": "job-v1", "path": os.path.abspath(path)},
    )


def compile_path(path: str, *, dry_run: bool = True) -> dict[str, Any]:
    """Load job-v1 from path and compile to artifacts."""
    job = jobspec.load(path)
    job["_source_path"] = os.path.abspath(path)
    return compile_job(job, dry_run=dry_run)
