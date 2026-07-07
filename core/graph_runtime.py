#!/usr/bin/env python3
"""Graph runtime — execute graph-v1 from pre-compiled artifacts only.

The runtime never compiles. Artifacts must be supplied in ctx before execution.

  artifacts.load → reference.resolve → engine.generate → output.persist
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import adapters
import jobspec

NODE_TYPES = (
    "artifacts.load",
    "reference.resolve",
    "engine.generate",
    "output.persist",
)


class GraphRuntimeError(ValueError):
    """Raised when a node cannot execute."""


def execute_node(
    node: dict[str, Any],
    inputs: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, Any]:
    """Run one graph node. Returns port-name → value outputs."""
    ntype = node.get("type")
    params = node.get("params") or {}
    handlers = {
        "artifacts.load": _exec_artifacts_load,
        "reference.resolve": _exec_reference_resolve,
        "engine.generate": _exec_engine_generate,
        "output.persist": _exec_output_persist,
    }
    if ntype not in handlers:
        raise GraphRuntimeError(f"Unknown node type {ntype!r}.")
    return handlers[ntype](inputs, params, ctx)


def _require_artifacts(ctx: dict[str, Any]) -> dict[str, Any]:
    artifacts = ctx.get("artifacts")
    if not isinstance(artifacts, dict):
        raise GraphRuntimeError(
            "Runtime artifacts required in ctx — runtime never compiles."
        )
    for key in ("product_spec", "creative_spec", "edit_specs", "provider_prompt"):
        if key in artifacts:
            raise GraphRuntimeError(
                f"Runtime must not consume {key!r} — load runtime-bundle only."
            )
    return artifacts


def _exec_artifacts_load(
    inputs: dict[str, Any],
    params: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, Any]:
    del inputs, params
    artifacts = _require_artifacts(ctx)
    plan = artifacts.get("execution_plan") or {}
    prompt_block = plan.get("prompt") or {}
    ctx["execution_plan"] = plan
    ctx["provider_prompt"] = {
        "positive": prompt_block.get("positive"),
        "negative": prompt_block.get("negative"),
    }
    ctx["compile_hash"] = artifacts.get("compile_hash")
    return {"artifacts": artifacts}


def _exec_reference_resolve(
    inputs: dict[str, Any],
    params: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, Any]:
    del ctx
    artifacts = inputs.get("artifacts") or {}
    execution = artifacts.get("execution_request") or {}
    reference = execution.get("reference")
    adapter_name = params.get("adapter", "local")
    job_dir = artifacts.get("job_dir", ".")
    adapter = adapters.get_reference(adapter_name)

    if isinstance(reference, list):
        resolved: list[str] = []
        for item in reference:
            path = adapter.resolve(item, job_dir)
            if path:
                resolved.append(path)
        return {"refs": resolved or None}

    path = adapter.resolve(reference, job_dir)
    return {"refs": path}


def _exec_engine_generate(
    inputs: dict[str, Any],
    params: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, Any]:
    del params
    artifacts = inputs.get("artifacts") or {}
    plan = artifacts.get("execution_plan")
    if not isinstance(plan, dict):
        raise GraphRuntimeError("engine.generate requires execution_plan in artifacts.")

    prompt_block = plan.get("prompt") or {}
    prompt = prompt_block.get("positive")
    if not prompt:
        raise GraphRuntimeError("engine.generate requires compiled positive prompt.")

    refs = inputs.get("refs")
    job_dir = artifacts.get("job_dir", ".")
    engine_adapter = adapters.get_engine(plan["provider"])

    dry_run = bool(ctx.get("dry_run", True))
    if dry_run:
        est = engine_adapter.execute_plan(plan, refs, job_dir, dry_run=True)
        ctx["cost_estimate"] = est
        return {"results": []}

    results = engine_adapter.execute_plan(plan, refs, job_dir, dry_run=False)
    return {"results": results}


def _exec_output_persist(
    inputs: dict[str, Any],
    params: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, Any]:
    results = inputs.get("results")
    artifacts = inputs.get("artifacts") or {}
    persist_ctx = artifacts.get("persist_context") or {}
    if not persist_ctx:
        raise GraphRuntimeError("output.persist requires persist_context in artifacts.")
    if not isinstance(results, list):
        raise GraphRuntimeError("output.persist requires results input.")

    if ctx.get("dry_run", True):
        return {"files": []}

    out_dir = params.get("out_dir") or (persist_ctx.get("output") or {}).get("dir")
    job_dir = artifacts.get("job_dir", ".")
    out_dir = out_dir if out_dir and os.path.isabs(out_dir) else os.path.join(
        job_dir, out_dir or "out"
    )
    adapter_name = params.get("adapter", "local")
    saved = adapters.get_output(adapter_name).persist(results, persist_ctx, out_dir)
    return {"files": saved}


def input_hash(payload: Any) -> str:
    """Stable hash for node input snapshots (cache / dedup)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
