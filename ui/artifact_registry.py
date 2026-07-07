#!/usr/bin/env python3
"""Artifact registry — disk-backed recent shots per product/campaign with feedback."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import workspace_runs

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_REGISTRY_VERSION = 1
_DEFAULT_LIMIT = 10

# drive-upload folder → product slug (JOGA starz-kid-images)
_DRIVE_FOLDER_SLUGS: dict[str, str] = {
    "starz-airboost": "airboost",
    "starz-footzen": "footzen",
    "starz-headband": "headband",
    "starz-massage-ball": "massage-ball",
    "starz-sps": "breathe-shin-pads",
    "starz-hairbands": "hairbands",
    "starz-ear-plugs": "ear-plugs",
    "starz-sleep-mask": "sleep-mask",
    "starz-cr7-bundle": "cr7-bundle",
    "starz-jp-bundle": "jp-bundle",
    "starz-ankle-weights": "ankle-weights",
}


def registry_path(root: Path, workspace_id: str | None = None) -> Path:
    d = workspace_runs.workspace_dir(root, workspace_id)
    return d / "artifact_registry.json"


def load_registry(root: Path, workspace_id: str | None = None) -> dict[str, Any]:
    path = registry_path(root, workspace_id)
    if not path.is_file():
        return {"version": _REGISTRY_VERSION, "artifacts": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("artifacts"), dict):
        data["artifacts"] = {}
    return data


def save_registry(root: Path, data: dict[str, Any], workspace_id: str | None = None) -> None:
    data["version"] = _REGISTRY_VERSION
    data["updated_at"] = time.time()
    path = registry_path(root, workspace_id)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _artifact_id(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]


def _slug_from_filename(name: str) -> str | None:
    """starz-hairbands-1.webp → hairbands; starz-ankle-weights-2.webp → ankle-weights."""
    lower = name.lower()
    m = re.match(r"starz-(.+?)-(\d+)\.(webp|png|jpe?g|gif)$", lower)
    if not m:
        m = re.match(r"starz-([a-z0-9-]+)\.(webp|png|jpe?g|gif)$", lower)
        if not m:
            return None
        part = m.group(1)
    else:
        part = m.group(1)
    aliases = {
        "breathe": "breathe-shin-pads",
        "sps": "breathe-shin-pads",
        "cr7-bundle": "cr7-bundle",
        "jp-bundle": "jp-bundle",
    }
    return aliases.get(part, part)


def _drive_folder_slug(folder_name: str) -> str | None:
    key = folder_name.strip().lower().replace("_", "-")
    if key in _DRIVE_FOLDER_SLUGS:
        return _DRIVE_FOLDER_SLUGS[key]
    return _slug_from_filename(f"starz-{key}-1.webp")


def _infer_source(path: Path, jobs_root: Path) -> str:
    parts = {p.lower() for p in path.parts}
    if "drive-upload" in parts:
        return "sheet_import"
    if "labeled" in parts:
        return "manual"
    return "job_run"


def _scan_image(
    img: Path,
    *,
    product_slug: str,
    jobs_root: Path,
    campaign_slug: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    path_str = img.resolve().as_posix()
    st = img.stat()
    return {
        "id": _artifact_id(path_str),
        "path": path_str,
        "name": img.name,
        "product_slug": product_slug,
        "campaign_slug": campaign_slug,
        "created_at": st.st_mtime,
        "source": source or _infer_source(img, jobs_root),
        "comment": "",
        "deleted_at": None,
        "delete_reason": None,
    }


def discover_images(jobs_root: Path, product_slug: str | None = None) -> list[dict[str, Any]]:
    """Scan out/ dirs, webp subdirs, drive-upload sheet imports."""
    if not jobs_root.is_dir():
        return []

    found: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    def add(row: dict[str, Any]) -> None:
        p = row["path"]
        if p in seen_paths:
            return
        if product_slug and row["product_slug"] != product_slug:
            return
        seen_paths.add(p)
        found.append(row)

    # Per-product out/ trees
    for child in sorted(jobs_root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name in ("labeled", "shared", "Craig Photos", "drive-upload-2026-07-06"):
            continue
        if child.name == "out":
            continue
        slug = child.name
        for pattern in ("*.webp", "*.png", "*.jpg", "*.jpeg"):
            for folder in [child / "out", child / "out" / "webp"]:
                if not folder.is_dir():
                    continue
                for img in folder.glob(pattern):
                    if img.suffix.lower() not in _IMAGE_EXTS:
                        continue
                    add(_scan_image(img, product_slug=slug, jobs_root=jobs_root))

    # Root-level out/ (e.g. ankle-weights at jobs root)
    root_out = jobs_root / "out"
    if root_out.is_dir():
        for img in root_out.rglob("*"):
            if not img.is_file() or img.suffix.lower() not in _IMAGE_EXTS:
                continue
            slug = _slug_from_filename(img.name)
            if not slug:
                continue
            add(_scan_image(img, product_slug=slug, jobs_root=jobs_root))

    # Google Sheet / Drive upload webps
    for drive_dir in sorted(jobs_root.glob("drive-upload-*")):
        if not drive_dir.is_dir():
            continue
        for product_dir in drive_dir.iterdir():
            if not product_dir.is_dir():
                continue
            slug = _drive_folder_slug(product_dir.name)
            if not slug:
                continue
            for img in product_dir.glob("*.webp"):
                add(
                    _scan_image(
                        img,
                        product_slug=slug,
                        jobs_root=jobs_root,
                        source="sheet_import",
                    )
                )

    # Run registry outputs (attach campaign when known)
    return found


def _merge_run_outputs(
    root: Path,
    workspace_id: str | None,
    discovered: list[dict[str, Any]],
    seen_paths: set[str],
) -> list[dict[str, Any]]:
    extra: list[dict[str, Any]] = []
    for run in workspace_runs.list_runs(root, workspace_id, limit=200):
        campaign = run.get("campaign_id")
        product = run.get("product_slug") or ""
        for out in run.get("outputs") or []:
            path_str = out.get("path") or ""
            if not path_str or path_str in seen_paths:
                continue
            p = Path(path_str)
            if not p.is_file():
                continue
            seen_paths.add(path_str)
            extra.append(
                {
                    "id": _artifact_id(path_str),
                    "path": path_str,
                    "name": out.get("name") or p.name,
                    "product_slug": product,
                    "campaign_slug": campaign,
                    "created_at": out.get("mtime") or p.stat().st_mtime,
                    "source": "job_run",
                    "comment": "",
                    "deleted_at": None,
                    "delete_reason": None,
                }
            )
    return discovered + extra


def sync_registry(
    root: Path,
    jobs_root: Path,
    workspace_id: str | None = None,
    product_slug: str | None = None,
) -> dict[str, Any]:
    """Merge disk discovery into registry; preserve comments and soft deletes."""
    data = load_registry(root, workspace_id)
    artifacts: dict[str, Any] = data["artifacts"]

    discovered = discover_images(jobs_root, product_slug=product_slug)
    seen = {a["path"] for a in discovered}
    discovered = _merge_run_outputs(root, workspace_id, discovered, seen)

    added = 0
    updated = 0
    for row in discovered:
        aid = row["id"]
        if aid not in artifacts:
            artifacts[aid] = row
            added += 1
        else:
            existing = artifacts[aid]
            for key in ("path", "name", "product_slug", "campaign_slug", "created_at", "source"):
                if existing.get(key) != row.get(key):
                    existing[key] = row[key]
                    updated += 1
            # Refresh mtime if file newer
            if row.get("created_at", 0) > existing.get("created_at", 0):
                existing["created_at"] = row["created_at"]

    data["artifacts"] = artifacts
    data["last_sync_at"] = time.time()
    save_registry(root, data, workspace_id)
    return {"added": added, "updated": updated, "total": len(artifacts)}


def list_artifacts(
    root: Path,
    *,
    workspace_id: str | None = None,
    product_slug: str | None = None,
    campaign_slug: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    include_deleted: bool = False,
) -> list[dict[str, Any]]:
    data = load_registry(root, workspace_id)
    rows: list[dict[str, Any]] = []
    for row in data["artifacts"].values():
        if not include_deleted and row.get("deleted_at"):
            continue
        if product_slug and row.get("product_slug") != product_slug:
            continue
        if campaign_slug and row.get("campaign_slug") != campaign_slug:
            continue
        path = row.get("path") or ""
        if path and not Path(path).is_file():
            continue
        rows.append(_enrich_row(row))
    rows.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return rows[:limit]


def get_artifact(root: Path, artifact_id: str, workspace_id: str | None = None) -> dict[str, Any]:
    data = load_registry(root, workspace_id)
    row = data["artifacts"].get(artifact_id)
    if not row:
        raise KeyError(artifact_id)
    return _enrich_row(row)


def update_comment(
    root: Path,
    artifact_id: str,
    comment: str,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    data = load_registry(root, workspace_id)
    row = data["artifacts"].get(artifact_id)
    if not row:
        raise KeyError(artifact_id)
    row["comment"] = comment.strip()
    row["updated_at"] = time.time()
    save_registry(root, data, workspace_id)
    return _enrich_row(row)


def soft_delete(
    root: Path,
    artifact_id: str,
    *,
    reason: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    data = load_registry(root, workspace_id)
    row = data["artifacts"].get(artifact_id)
    if not row:
        raise KeyError(artifact_id)
    row["deleted_at"] = time.time()
    if reason:
        row["delete_reason"] = reason.strip()
    save_registry(root, data, workspace_id)
    return _enrich_row(row)


def _enrich_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    path = row.get("path") or ""
    if path:
        out["url"] = f"/media?path={path}"
    return out
