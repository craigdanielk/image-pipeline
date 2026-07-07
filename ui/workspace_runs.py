#!/usr/bin/env python3
"""Workspace run registry — disk-backed queue for enterprise greenfield MVP."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


def workspace_dir(root: Path, workspace_id: str | None = None) -> Path:
    wid = (workspace_id or "default").strip() or "default"
    d = root / "workspace" / wid
    d.mkdir(parents=True, exist_ok=True)
    return d


def workspace_meta(root: Path, workspace_id: str | None = None) -> dict[str, Any]:
    d = workspace_dir(root, workspace_id)
    meta_path = d / "workspace.json"
    if not meta_path.is_file():
        meta = {
            "id": workspace_id or "default",
            "name": "My Workspace",
            "created_at": time.time(),
        }
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return meta
    return json.loads(meta_path.read_text(encoding="utf-8"))


def runs_dir(root: Path, workspace_id: str | None = None) -> Path:
    d = workspace_dir(root, workspace_id) / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_run(
    root: Path,
    *,
    product_slug: str,
    campaign_id: str,
    job_path: str,
    job_dir: str,
    shot_id: str,
    output_types: list[str],
    changes: list[str],
    credits_estimated: int | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    now = time.time()
    record = {
        "id": run_id,
        "status": "queued",
        "product_slug": product_slug,
        "campaign_id": campaign_id,
        "shot_id": shot_id,
        "job_path": job_path,
        "job_dir": job_dir,
        "output_types": output_types,
        "changes": changes,
        "credits_estimated": credits_estimated,
        "outputs": [],
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    path = runs_dir(root, workspace_id) / f"{run_id}.json"
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def update_run(
    root: Path,
    run_id: str,
    *,
    status: str | None = None,
    outputs: list[dict[str, Any]] | None = None,
    error: str | None = None,
    credits_estimated: int | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    path = runs_dir(root, workspace_id) / f"{run_id}.json"
    if not path.is_file():
        raise FileNotFoundError(run_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    if status is not None:
        record["status"] = status
    if outputs is not None:
        record["outputs"] = outputs
    if error is not None:
        record["error"] = error
    if credits_estimated is not None:
        record["credits_estimated"] = credits_estimated
    record["updated_at"] = time.time()
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def list_runs(root: Path, workspace_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    d = runs_dir(root, workspace_id)
    records: list[dict[str, Any]] = []
    for path in d.glob("run-*.json"):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    records.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return records[:limit]


def get_run(root: Path, run_id: str, workspace_id: str | None = None) -> dict[str, Any]:
    path = runs_dir(root, workspace_id) / f"{run_id}.json"
    if not path.is_file():
        raise FileNotFoundError(run_id)
    return json.loads(path.read_text(encoding="utf-8"))


def approvals_path(root: Path, workspace_id: str | None = None) -> Path:
    d = workspace_dir(root, workspace_id)
    return d / "asset_approvals.json"


def load_approvals(root: Path, workspace_id: str | None = None) -> dict[str, str]:
    path = approvals_path(root, workspace_id)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def set_approval(
    root: Path, asset_path: str, status: str, workspace_id: str | None = None
) -> dict[str, str]:
    if status not in ("pending", "approved", "rejected"):
        raise ValueError(f"invalid approval status: {status}")
    data = load_approvals(root, workspace_id)
    data[asset_path] = status
    path = approvals_path(root, workspace_id)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data
