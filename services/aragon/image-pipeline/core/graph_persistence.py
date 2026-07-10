#!/usr/bin/env python3
"""Graph persistence — optional DB layer for graph runs and compile cache.

Mirrors tracking.py: dep-free urllib against PostgREST, off by default.
Enable with IMAGE_PIPELINE_TRACK=1 + SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

import tracking

_ENABLED: bool | None = None


def enabled(job_dir: str = ".") -> bool:
    """True when tracking env is configured (same gate as tracking.py)."""
    return tracking.enabled(job_dir)


def _rest(method: str, path: str, body: dict | None = None, params: str = ""):
    return tracking._rest(method, path, body, params)


def _safe(fn, *args, **kwargs):
    return tracking._safe(fn, *args, **kwargs)


def find_idempotent_run(
    tenant_id: str | None,
    idempotency_key: str,
) -> dict[str, Any] | None:
    """Return cached completed graph_run result when key matches, else None."""
    if not idempotency_key or not enabled():
        return None

    def _do():
        if tenant_id:
            q = (
                f"?tenant_id=eq.{tenant_id}"
                f"&idempotency_key=eq.{idempotency_key}"
                f"&status=eq.completed&select=id,result,dry_run&limit=1"
            )
        else:
            q = (
                f"?tenant_id=is.null"
                f"&idempotency_key=eq.{idempotency_key}"
                f"&status=eq.completed&select=id,result,dry_run&limit=1"
            )
        rows = _rest("GET", "graph_runs", params=q)
        if not rows:
            return None
        row = rows[0]
        result = row.get("result")
        if not isinstance(result, dict):
            return None
        result = dict(result)
        result["_graph_run_id"] = row["id"]
        result["_idempotent_hit"] = True
        return result

    return _safe(_do)


def start_graph_run(
    *,
    tenant_id: str | None,
    graph_id: str,
    graph_snapshot: dict[str, Any],
    compile_hash: str | None = None,
    idempotency_key: str | None = None,
    parent_run_id: str | None = None,
    dry_run: bool = True,
) -> str | None:
    """Insert a graph_runs row in running state. Returns uuid or None."""
    if not enabled():
        return None

    def _do():
        row = _rest("POST", "graph_runs", {
            "tenant_id": tenant_id,
            "graph_id": graph_id,
            "graph_snapshot": graph_snapshot,
            "compile_hash": compile_hash,
            "idempotency_key": idempotency_key,
            "parent_run_id": parent_run_id,
            "status": "running",
            "dry_run": dry_run,
        })
        return row[0]["id"]

    return _safe(_do)


def complete_graph_run(
    run_id: str | None,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Mark graph_runs completed or failed."""
    if not run_id or not enabled():
        return
    body: dict[str, Any] = {"status": status}
    if result is not None:
        body["result"] = result
    if error is not None:
        body["error"] = error[:2000]
    _safe(_rest, "PATCH", "graph_runs", body, params=f"?id=eq.{run_id}")


def record_node_run(
    graph_run_id: str | None,
    *,
    node_id: str,
    node_type: str,
    status: str,
    input_hash: str | None = None,
    output_snapshot: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Upsert graph_node_runs row (best-effort)."""
    if not graph_run_id or not enabled():
        return

    def _do():
        existing = _rest(
            "GET",
            "graph_node_runs",
            params=(
                f"?graph_run_id=eq.{graph_run_id}"
                f"&node_id=eq.{node_id}&select=id&limit=1"
            ),
        )
        body = {
            "graph_run_id": graph_run_id,
            "node_id": node_id,
            "node_type": node_type,
            "status": status,
            "input_hash": input_hash,
            "output_snapshot": output_snapshot,
            "error": error[:2000] if error else None,
        }
        if existing:
            _rest(
                "PATCH",
                "graph_node_runs",
                body,
                params=f"?id=eq.{existing[0]['id']}",
            )
        else:
            _rest("POST", "graph_node_runs", body)

    _safe(_do)


def list_node_runs(graph_run_id: str | None) -> list[dict[str, Any]]:
    """Read-side snapshot: all graph_node_runs rows for a graph_run_id.

    Mirrors the write-side query pattern in record_node_run. Lets a client
    reconnecting mid-run resync per-node statuses before subscribing to a
    live stream.
    """
    if not graph_run_id or not enabled():
        return []

    def _do():
        return _rest(
            "GET",
            "graph_node_runs",
            params=(
                f"?graph_run_id=eq.{graph_run_id}"
                f"&select=*&order=created_at.asc"
            ),
        )

    rows = _safe(_do)
    return rows if isinstance(rows, list) else []


def lookup_compile_cache(
    tenant_id: str | None,
    content_hash: str,
) -> dict[str, Any] | None:
    """Return compile_artifacts row by content_hash, or None."""
    if not content_hash or not enabled():
        return None

    def _do():
        if tenant_id:
            q = f"?tenant_id=eq.{tenant_id}&content_hash=eq.{content_hash}&limit=1"
        else:
            q = f"?tenant_id=is.null&content_hash=eq.{content_hash}&limit=1"
        rows = _rest("GET", "compile_artifacts", params=q)
        return rows[0] if rows else None

    return _safe(_do)


def store_compile_artifact(
    *,
    tenant_id: str | None,
    content_hash: str,
    product_spec: dict[str, Any],
    creative_spec: dict[str, Any],
    execution_request: dict[str, Any],
    provider_prompt: dict[str, Any],
) -> str | None:
    """Insert compile cache row (ignore conflict). Returns id or None."""
    if not content_hash or not enabled():
        return None

    def _do():
        row = _rest("POST", "compile_artifacts", {
            "tenant_id": tenant_id,
            "content_hash": content_hash,
            "product_spec": product_spec,
            "creative_spec": creative_spec,
            "execution_request": execution_request,
            "provider_prompt": provider_prompt,
        })
        return row[0]["id"]

    return _safe(_do)


def lint_for_secrets(graph: dict[str, Any]) -> None:
    """Reuse tracking secret lint before persisting graph snapshots."""
    tracking.lint_for_secrets(graph)
