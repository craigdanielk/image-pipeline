#!/usr/bin/env python3
"""Graph runner — execute from compile/ manifest only.

Compile phase (job_compiler) runs BEFORE execution when source is job-v1.
The runtime never compiles inside graph nodes.

Usage:
  python core/run_graph.py jobs/<name>.job.json --dry-run
  python core/run_graph.py compile/<job-id>/ --dry-run
  python core/compile_job.py jobs/<name>.job.json --compile-dir compile/<job-id>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import compile_bundle  # noqa: E402
import graph_ir  # noqa: E402
import graph_persistence  # noqa: E402
import graph_runtime  # noqa: E402
import job_compiler  # noqa: E402


def _gather_inputs(
    node_id: str,
    edges: list[dict[str, Any]],
    port_outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Wire upstream edge outputs into this node's input ports."""
    inputs: dict[str, Any] = {}
    for edge in edges:
        dst = edge.get("to") or {}
        if dst.get("node") != node_id:
            continue
        src = edge.get("from") or {}
        src_node = src.get("node")
        src_port = src.get("port")
        dst_port = dst.get("port")
        if not src_node or not src_port or not dst_port:
            continue
        upstream = port_outputs.get(src_node) or {}
        if src_port in upstream:
            inputs[dst_port] = upstream[src_port]
    return inputs


def _node_index(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {n["id"]: n for n in graph["nodes"]}


def _maybe_store_compile_cache(
    compile_dir: str | None,
    tenant_id: str | None,
    track: bool,
    compile_hash: str | None,
) -> None:
    if not track or not compile_dir or not compile_hash:
        return
    if graph_persistence.lookup_compile_cache(tenant_id, compile_hash):
        return
    try:
        full = compile_bundle.load_compiler_bundle(compile_dir)
        graph_persistence.store_compile_artifact(
            tenant_id=tenant_id,
            content_hash=compile_hash,
            product_spec=full["product_spec"],
            creative_spec=full["creative_spec"],
            execution_request=full["execution_request"],
            provider_prompt=full["provider_prompt"],
        )
    except Exception:
        pass


def run_graph(
    graph: dict[str, Any],
    *,
    runtime_bundle: dict[str, Any] | None = None,
    dry_run: bool = True,
    track: bool = False,
    tenant_id: str | None = None,
    idempotency_key: str | None = None,
    parent_run_id: str | None = None,
    compile_dir: str | None = None,
) -> dict[str, Any]:
    """Execute a validated execution graph from pre-compiled runtime bundle."""
    if runtime_bundle is None:
        raise ValueError("runtime_bundle is required — compile before execute.")

    graph = runtime_bundle["graph"]
    artifacts_view = compile_bundle.runtime_artifacts_view(runtime_bundle)
    dry_run = bool(runtime_bundle.get("dry_run", dry_run))

    graph_ir.validate(graph)

    if track:
        os.environ["IMAGE_PIPELINE_TRACK"] = "1"

    if idempotency_key:
        cached = graph_persistence.find_idempotent_run(tenant_id, idempotency_key)
        if cached is not None:
            print(f"  [run_graph] idempotent hit for key={idempotency_key!r}")
            return cached

    job_id = artifacts_view.get("job_id") or graph.get("metadata", {}).get("execution_id", "unknown")
    compile_hash = artifacts_view.get("compile_hash")

    ctx: dict[str, Any] = {
        "graph": graph,
        "artifacts": artifacts_view,
        "dry_run": dry_run,
        "track": track,
        "tenant_id": tenant_id,
        "execution_plan": artifacts_view.get("execution_plan"),
        "compile_hash": compile_hash,
    }

    graph_run_id = None
    if track:
        try:
            graph_persistence.lint_for_secrets(graph)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        graph_run_id = graph_persistence.start_graph_run(
            tenant_id=tenant_id,
            graph_id=graph["id"],
            graph_snapshot=graph,
            compile_hash=compile_hash,
            idempotency_key=idempotency_key,
            parent_run_id=parent_run_id,
            dry_run=dry_run,
        )

    node_by_id = _node_index(graph)
    edges = graph.get("edges") or []
    order = graph_ir.topological_sort(graph)
    port_outputs: dict[str, dict[str, Any]] = {}

    try:
        for nid in order:
            node = node_by_id[nid]
            inputs = _gather_inputs(nid, edges, port_outputs)
            in_hash = graph_runtime.input_hash({"node": nid, "inputs": inputs})

            if track and graph_run_id:
                graph_persistence.record_node_run(
                    graph_run_id,
                    node_id=nid,
                    node_type=node["type"],
                    status="running",
                    input_hash=in_hash,
                )

            outputs = graph_runtime.execute_node(node, inputs, ctx)
            port_outputs[nid] = outputs

            if track and graph_run_id:
                graph_persistence.record_node_run(
                    graph_run_id,
                    node_id=nid,
                    node_type=node["type"],
                    status="completed",
                    input_hash=in_hash,
                    output_snapshot=outputs,
                )

        _maybe_store_compile_cache(compile_dir, tenant_id, track, compile_hash)

        plan = artifacts_view.get("execution_plan") or {}
        prompt = (plan.get("prompt") or {}).get("positive")

        result: dict[str, Any] = {
            "id": job_id,
            "graph_id": graph["id"],
            "dry_run": dry_run,
        }
        if prompt:
            result["prompt"] = prompt
        if dry_run and ctx.get("cost_estimate") is not None:
            result["cost"] = ctx["cost_estimate"]
        if not dry_run:
            persist_out = port_outputs.get("persist") or {}
            saved = persist_out.get("files")
            if saved:
                result["saved"] = saved
        if artifacts_view.get("execution_plan"):
            result["execution_plan"] = artifacts_view["execution_plan"]
        if compile_hash:
            result["compile_hash"] = compile_hash
        if compile_dir:
            result["compile_dir"] = os.path.abspath(compile_dir)
        if graph_run_id:
            result["graph_run_id"] = graph_run_id

        if track and graph_run_id:
            store_result = {k: v for k, v in result.items() if not k.startswith("_")}
            graph_persistence.complete_graph_run(
                graph_run_id,
                status="completed",
                result=store_result,
            )

        return result

    except Exception as exc:
        if track and graph_run_id:
            graph_persistence.complete_graph_run(
                graph_run_id,
                status="failed",
                error=str(exc),
            )
        raise


def run_compile_dir(
    compile_dir: str,
    *,
    track: bool = False,
    tenant_id: str | None = None,
    idempotency_key: str | None = None,
    parent_run_id: str | None = None,
) -> dict[str, Any]:
    """Execute from existing compile/ directory — no compile phase."""
    runtime_bundle = compile_bundle.load_runtime_bundle(compile_dir)
    return run_graph(
        runtime_bundle["graph"],
        runtime_bundle=runtime_bundle,
        track=track,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        parent_run_id=parent_run_id,
        compile_dir=compile_dir,
    )


def run_job_path(
    job_path: str,
    *,
    dry_run: bool = True,
    track: bool = False,
    tenant_id: str | None = None,
    idempotency_key: str | None = None,
    parent_run_id: str | None = None,
    compile_dir: str | None = None,
    keep_compile: bool = True,
) -> dict[str, Any]:
    """Compile job-v1 to compile/ then execute — single entry for CLI wrappers."""
    import jobspec

    loaded = jobspec.load(job_path)
    job_dir = loaded.get("_dir", ".")
    target_dir = compile_dir or compile_bundle.default_compile_dir(job_dir, loaded["id"])

    if keep_compile and os.path.isdir(target_dir) and compile_bundle.is_compile_dir(target_dir):
        manifest = compile_bundle.load_manifest(target_dir)
        if manifest.get("dry_run") == dry_run:
            print(f"  [run_graph] reusing compile dir: {target_dir}")
            return run_compile_dir(
                target_dir,
                track=track,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                parent_run_id=parent_run_id,
            )

    job_compiler.compile_path_to_dir(job_path, target_dir, dry_run=dry_run)
    return run_compile_dir(
        target_dir,
        track=track,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        parent_run_id=parent_run_id,
    )


def main(argv: list[str] | None = None) -> dict[str, Any]:
    ap = argparse.ArgumentParser(description="Compile (if job-v1) then execute from compile/.")
    ap.add_argument(
        "target",
        help="path to .job.json source OR compile/ directory with manifest.json",
    )
    ap.add_argument("--dry-run", action="store_true", help="cost estimate only")
    ap.add_argument("--track", action="store_true", help="mirror run into Supabase")
    ap.add_argument("--tenant-id", default=None, help="optional tenant uuid")
    ap.add_argument("--idempotency-key", default=None, help="re-run safe cache key")
    ap.add_argument("--parent-run-id", default=None, help="lineage parent graph_run uuid")
    ap.add_argument("--compile-dir", default=None, help="override compile/ output path for job-v1")
    ap.add_argument(
        "--no-keep-compile",
        action="store_true",
        help="always recompile job-v1 even if compile/ exists",
    )
    a = ap.parse_args(argv)

    if compile_bundle.is_compile_dir(a.target):
        result = run_compile_dir(
            a.target,
            track=a.track,
            tenant_id=a.tenant_id,
            idempotency_key=a.idempotency_key,
            parent_run_id=a.parent_run_id,
        )
    else:
        result = run_job_path(
            a.target,
            dry_run=a.dry_run,
            track=a.track,
            tenant_id=a.tenant_id,
            idempotency_key=a.idempotency_key,
            parent_run_id=a.parent_run_id,
            compile_dir=a.compile_dir,
            keep_compile=not a.no_keep_compile,
        )
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    main()
