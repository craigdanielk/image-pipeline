#!/usr/bin/env python3
"""Graph compiler — lower compile artifacts to execution-only graph-v1 DAGs.

Compile phase (job_compiler) happens BEFORE graph execution. Graph IR carries
handles only — no job_snapshot business blobs.
"""
from __future__ import annotations

import json
import os
from typing import Any

import graph_ir
import job_compiler

SCHEMA_VERSION = "graph-v1"
ARTIFACTS_SCHEMA = job_compiler.ARTIFACTS_SCHEMA


class GraphCompilerError(ValueError):
    """Raised when artifacts cannot be lowered to graph-v1."""


def compile_path(path: str, *, dry_run: bool = True) -> dict[str, Any]:
    """Load job-v1, compile artifacts, return execution graph."""
    artifacts = job_compiler.compile_path(path, dry_run=dry_run)
    return compile_execution_graph(artifacts)


def compile_job(job: dict[str, Any], *, dry_run: bool = True) -> dict[str, Any]:
    """Compile in-memory job-v1 to execution graph."""
    artifacts = job_compiler.compile_job(job, dry_run=dry_run)
    return compile_execution_graph(artifacts)


def compile_job_path(path: str, *, dry_run: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (artifacts, execution_graph) for run_graph entry."""
    artifacts = job_compiler.compile_path(path, dry_run=dry_run)
    return artifacts, compile_execution_graph(artifacts)


def compile_execution_graph(artifacts: dict[str, Any]) -> dict[str, Any]:
    """Lower compile-artifacts-v1 to a 4-node execution DAG."""
    if artifacts.get("schema_version") != ARTIFACTS_SCHEMA:
        raise GraphCompilerError(
            f"Expected schema_version {ARTIFACTS_SCHEMA!r}, got {artifacts.get('schema_version')!r}."
        )

    execution = artifacts["execution_request"]
    reference = execution.get("reference")
    output = execution.get("output") or {}
    if isinstance(reference, list):
        ref_port_type = "image_path_list"
    elif reference is None:
        ref_port_type = "image_path"
    else:
        ref_port_type = "image_path"
    job_id = artifacts["job_id"]

    nodes: list[dict[str, Any]] = [
        {
            "id": "load",
            "type": "artifacts.load",
            "params": {},
            "ports": {"out": {"artifacts": {"type": "compile_artifacts"}}},
        },
        {
            "id": "resolve_refs",
            "type": "reference.resolve",
            "params": {"adapter": "local"},
            "ports": {
                "in": {"artifacts": {"type": "compile_artifacts"}},
                "out": {"refs": {"type": ref_port_type}},
            },
        },
        {
            "id": "generate",
            "type": "engine.generate",
            "params": {},
            "ports": {
                "in": {
                    "artifacts": {"type": "compile_artifacts"},
                    "refs": {"type": ref_port_type},
                },
                "out": {"results": {"type": "engine_results"}},
            },
        },
        {
            "id": "persist",
            "type": "output.persist",
            "params": {
                "imgtype": output.get("imgtype", "hero"),
                "out_dir": output.get("dir"),
                "adapter": "local",
            },
            "ports": {
                "in": {
                    "artifacts": {"type": "compile_artifacts"},
                    "results": {"type": "engine_results"},
                },
                "out": {"files": {"type": "file_list"}},
            },
        },
    ]

    edges = [
        {"from": {"node": "load", "port": "artifacts"}, "to": {"node": "resolve_refs", "port": "artifacts"}},
        {"from": {"node": "load", "port": "artifacts"}, "to": {"node": "generate", "port": "artifacts"}},
        {"from": {"node": "load", "port": "artifacts"}, "to": {"node": "persist", "port": "artifacts"}},
        {"from": {"node": "resolve_refs", "port": "refs"}, "to": {"node": "generate", "port": "refs"}},
        {"from": {"node": "generate", "port": "results"}, "to": {"node": "persist", "port": "results"}},
    ]

    graph = {
        "schema_version": SCHEMA_VERSION,
        "id": f"{job_id}-exec-graph",
        "metadata": {
            "compiled_from": f"{ARTIFACTS_SCHEMA}:{job_id}",
            "compile_hash": artifacts.get("compile_hash"),
            "execution_id": job_id,
        },
        "nodes": nodes,
        "edges": edges,
    }
    graph_ir.validate(graph)
    return graph


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python graph_compiler.py <job.json>", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(compile_path(sys.argv[1]), indent=2))
