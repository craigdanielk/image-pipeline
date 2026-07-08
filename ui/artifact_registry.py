#!/usr/bin/env python3
"""Artifact registry — disk-backed recent shots per product/campaign with feedback."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

import workspace_runs

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_REGISTRY_VERSION = 1
_DEFAULT_LIMIT = 24

_STATUS_APPROVED = "approved"
_STATUS_REJECTED = "rejected"
_STATUS_PENDING = "pending"

_VERSION_SUFFIX_RE = re.compile(r"-v\d+$")

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
        "approved_at": None,
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

    # out/ trees at any depth — product is nested directly under jobs_root for a
    # flat consumer (jobs/<product>/out/) or under an intermediate campaign folder
    # (jobs/<campaign>/<product>/out/). Filename convention (starz-<product>-N.ext)
    # takes priority over the containing folder name since curated webp exports can
    # land in a shared campaign-level out/ that isn't itself a product folder.
    _noise = {"labeled", "shared", "craig photos", "compile", "refs"}
    for out_dir in sorted(jobs_root.rglob("out")):
        if not out_dir.is_dir():
            continue
        rel_parts = out_dir.relative_to(jobs_root).parts[:-1]
        if any(part.lower() in _noise or part.lower().startswith("drive-upload") for part in rel_parts):
            continue
        # A campaign-level catch-all (jobs/<campaign>/out/) sits one level above where
        # per-product folders live — trust the filename there. A real per-product
        # folder (jobs/<campaign>/<product>/out/, or jobs/<product>/out/ when flat)
        # is ground truth — trust its own name over a filename-parsing guess.
        campaign_level = out_dir.parent.parent == jobs_root
        for pattern in ("*.webp", "*.png", "*.jpg", "*.jpeg"):
            for folder in (out_dir, out_dir / "webp"):
                if not folder.is_dir():
                    continue
                for img in folder.glob(pattern):
                    if img.suffix.lower() not in _IMAGE_EXTS:
                        continue
                    if campaign_level:
                        slug = _slug_from_filename(img.name) or out_dir.parent.name
                    else:
                        slug = out_dir.parent.name or _slug_from_filename(img.name)
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
                    "approved_at": None,
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


def _status_of(row: dict[str, Any]) -> str:
    if row.get("deleted_at"):
        return _STATUS_REJECTED
    if row.get("approved_at"):
        return _STATUS_APPROVED
    return _STATUS_PENDING


def _group_key(name: str) -> str:
    """Collapse version iterations of the same shot: shot-4-logo-fix-v3.png -> shot-4-logo-fix."""
    stem = name.rsplit(".", 1)[0]
    job_id = stem.split("__", 1)[0]
    return _VERSION_SUFFIX_RE.sub("", job_id)


def _filtered_rows(
    root: Path,
    *,
    workspace_id: str | None = None,
    product_slug: str | None = None,
    campaign_slug: str | None = None,
    include_deleted: bool = False,
    status: str | None = None,
) -> list[dict[str, Any]]:
    data = load_registry(root, workspace_id)
    rows: list[dict[str, Any]] = []
    for row in data["artifacts"].values():
        if status:
            if _status_of(row) != status:
                continue
        elif not include_deleted and row.get("deleted_at"):
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
    return rows


def list_artifacts(
    root: Path,
    *,
    workspace_id: str | None = None,
    product_slug: str | None = None,
    campaign_slug: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
    include_deleted: bool = False,
    status: str | None = None,
) -> list[dict[str, Any]]:
    rows = _filtered_rows(
        root,
        workspace_id=workspace_id,
        product_slug=product_slug,
        campaign_slug=campaign_slug,
        include_deleted=include_deleted,
        status=status,
    )
    return rows[offset : offset + limit]


def count_artifacts(
    root: Path,
    *,
    workspace_id: str | None = None,
    product_slug: str | None = None,
    campaign_slug: str | None = None,
    include_deleted: bool = False,
    status: str | None = None,
) -> int:
    return len(
        _filtered_rows(
            root,
            workspace_id=workspace_id,
            product_slug=product_slug,
            campaign_slug=campaign_slug,
            include_deleted=include_deleted,
            status=status,
        )
    )


def get_artifact(root: Path, artifact_id: str, workspace_id: str | None = None) -> dict[str, Any]:
    data = load_registry(root, workspace_id)
    row = data["artifacts"].get(artifact_id)
    if not row:
        raise KeyError(artifact_id)
    return _enrich_row(row)


def update_comment(
    root: Path,
    artifact_id: str,
    comment: str | None,
    workspace_id: str | None = None,
    *,
    drive_url: str | None = None,
) -> dict[str, Any]:
    data = load_registry(root, workspace_id)
    row = data["artifacts"].get(artifact_id)
    if not row:
        raise KeyError(artifact_id)
    if comment is not None:
        row["comment"] = comment.strip()
    if drive_url is not None:
        row["drive_url"] = drive_url.strip() or None
    row["updated_at"] = time.time()
    save_registry(root, data, workspace_id)
    return _enrich_row(row)


def soft_delete(
    root: Path,
    artifact_id: str,
    *,
    reason: str | None = None,
    workspace_id: str | None = None,
    remove_file: bool = True,
) -> dict[str, Any]:
    data = load_registry(root, workspace_id)
    row = data["artifacts"].get(artifact_id)
    if not row:
        raise KeyError(artifact_id)
    row["deleted_at"] = time.time()
    row["approved_at"] = None
    if reason:
        row["delete_reason"] = reason.strip()
    # Rejection removes the render from the product's out/ dir so rejected
    # images never linger as deliverables. Moved (not unlinked) into a sibling
    # _rejected/ dir: recoverable, thumbnail keeps resolving via /media, and
    # the reason stays attached for regen prompts.
    if remove_file:
        p = Path(row.get("path") or "")
        if p.is_file() and "out" in p.parts:
            # climb to the out/ dir (file may sit in out/ or out/webp/), trash
            # lands beside it — never inside it, or discovery re-scans the file
            out_dir = p.parent
            while out_dir.name != "out" and out_dir != out_dir.parent:
                out_dir = out_dir.parent
            trash = out_dir.parent / "_rejected"
            trash.mkdir(parents=True, exist_ok=True)
            dest = trash / p.name
            n = 1
            while dest.exists():
                dest = trash / f"{p.stem}.{n}{p.suffix}"
                n += 1
            shutil.move(str(p), str(dest))
            row["path"] = str(dest)
            row["file_removed_at"] = time.time()
    save_registry(root, data, workspace_id)
    return _enrich_row(row)


def approve_artifact(
    root: Path,
    artifact_id: str,
    *,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    data = load_registry(root, workspace_id)
    row = data["artifacts"].get(artifact_id)
    if not row:
        raise KeyError(artifact_id)
    row["approved_at"] = time.time()
    row["deleted_at"] = None
    row["delete_reason"] = None
    save_registry(root, data, workspace_id)
    return _enrich_row(row)


def restore_artifact(
    root: Path,
    artifact_id: str,
    *,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Undo a rejection — back to pending, file untouched (it was never deleted)."""
    data = load_registry(root, workspace_id)
    row = data["artifacts"].get(artifact_id)
    if not row:
        raise KeyError(artifact_id)
    row["deleted_at"] = None
    row["delete_reason"] = None
    save_registry(root, data, workspace_id)
    return _enrich_row(row)


def _enrich_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    path = row.get("path") or ""
    if path:
        out["url"] = f"/media?path={path}"
    out["status"] = _status_of(row)
    out["group_key"] = _group_key(row.get("name") or "")
    return out
