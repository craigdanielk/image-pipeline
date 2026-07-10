#!/usr/bin/env python3
"""STATE RECONCILER — registry ⇄ disk ⇄ Drive divergence detector.

The compiler promises a single source of truth for what images exist, but three
stores drift apart in practice:

  1. the artifact registry (JSON rows: the operator-facing index)
  2. the disk (out/ trees, drive-upload/ webps: the actual bytes)
  3. Google Drive uploads (webps synced down, sometimes with no registry row)

When a disk sweep moves files without updating registry paths, rows point at
paths that no longer exist. The old behaviour SILENTLY HID those rows from the
gallery (a `Path.is_file()` filter dropped them), so 159 real images went
invisible with no signal. That is the exact failure this module exists to make
LOUD instead: divergence is classified, counted, and reported — never hidden.

Divergence taxonomy (every row / disk file lands in exactly one bucket):

  ok        registry row whose path resolves to a real file on disk
  broken    registry row whose file is missing/moved on disk — FLAGGED, not hidden
  unlinked  a disk image (e.g. a Drive upload) with no registry row pointing at it
  orphaned  a registry row with no usable path at all (empty/blank path field)

This module is provider- and tenant-agnostic and dep-free (stdlib only), in
keeping with the bare-python3 core promise. It operates on a plain registry dict
(the `{"artifacts": {id: row}}` shape ui/artifact_registry.py persists) plus a
jobs_root Path; it mutates each row in place with a `reconcile_status` and a
`broken` flag, and returns a divergence report the caller can persist.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# reconcile_status values stamped onto each registry row
STATUS_OK = "ok"
STATUS_BROKEN = "broken"
STATUS_ORPHANED = "orphaned"


def _iter_disk_images(jobs_root: Path) -> Iterable[Path]:
    """Every image file under jobs_root, at any depth. Dep-free rglob walk.

    Used to find disk bytes that no registry row references (the `unlinked`
    bucket). Drive uploads land under drive-upload-*/ but we scan the whole tree
    so a moved file also surfaces as unlinked at its new location, not just
    broken at its old one.
    """
    if not jobs_root or not jobs_root.is_dir():
        return
    for p in jobs_root.rglob("*"):
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTS:
            yield p


def _norm(path_str: str) -> str:
    """Resolve to an absolute posix path for stable set membership tests."""
    try:
        return Path(path_str).resolve().as_posix()
    except (OSError, ValueError):
        return path_str


def reconcile_registry(
    registry: dict[str, Any],
    jobs_root: Path,
    *,
    now: float,
    scan_disk: bool = True,
    include_deleted: bool = False,
    exists: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Classify every registry row against disk; return a divergence report.

    Mutates each row in `registry["artifacts"]` in place, stamping:
      row["reconcile_status"] ∈ {ok, broken, orphaned}
      row["broken"]          bool  (True for broken/orphaned — surfaced in UI)
      row["reconciled_at"]   float (the `now` passed in)

    `exists` lets a caller inject a filesystem probe (tests); defaults to
    Path.is_file. `scan_disk` toggles the unlinked sweep (skip it for a fast
    row-only pass). Deleted rows keep their own path (they were moved to a
    _rejected/ sibling, not unlinked) and are skipped unless include_deleted.

    The returned report is JSON-serialisable and carries the four divergence
    counts plus per-bucket entry lists for the operator report.
    """
    file_exists = exists or (lambda s: bool(s) and Path(s).is_file())
    artifacts: dict[str, Any] = registry.get("artifacts") or {}

    counts = {"ok": 0, "broken": 0, "unlinked": 0, "orphaned": 0}
    broken_entries: list[dict[str, Any]] = []
    orphaned_entries: list[dict[str, Any]] = []
    linked_paths: set[str] = set()

    for aid, row in artifacts.items():
        path_str = (row.get("path") or "").strip()
        deleted = bool(row.get("deleted_at"))
        if deleted and not include_deleted:
            # A rejected row's file lives in a _rejected/ sibling on purpose;
            # still record its path as linked so it never counts as unlinked.
            if path_str:
                linked_paths.add(_norm(path_str))
            continue

        if not path_str:
            row["reconcile_status"] = STATUS_ORPHANED
            row["broken"] = True
            row["reconciled_at"] = now
            counts["orphaned"] += 1
            orphaned_entries.append({"id": aid, "name": row.get("name") or ""})
            continue

        linked_paths.add(_norm(path_str))
        if file_exists(path_str):
            row["reconcile_status"] = STATUS_OK
            row["broken"] = False
            row["reconciled_at"] = now
            counts["ok"] += 1
        else:
            # Moved/missing file: FLAG the row broken — do not hide it. This is
            # the divergence the operator must see, with the last-known path so
            # they can locate or re-sync the moved bytes.
            row["reconcile_status"] = STATUS_BROKEN
            row["broken"] = True
            row["reconciled_at"] = now
            counts["broken"] += 1
            broken_entries.append(
                {
                    "id": aid,
                    "name": row.get("name") or "",
                    "path": path_str,
                    "product_slug": row.get("product_slug"),
                    "source": row.get("source"),
                }
            )

    unlinked_entries: list[dict[str, Any]] = []
    if scan_disk:
        for img in _iter_disk_images(jobs_root):
            if _norm(img.as_posix()) in linked_paths:
                continue
            # Disk bytes no registry row points at — most often a Drive upload
            # synced down before the registry caught up. Report it so it can be
            # linked, never leave it as a floating file.
            unlinked_entries.append(
                {
                    "path": img.as_posix(),
                    "name": img.name,
                    "source": "drive" if "drive-upload" in img.as_posix().lower() else "disk",
                }
            )
        counts["unlinked"] = len(unlinked_entries)

    report = {
        "generated_at": now,
        "jobs_root": str(jobs_root),
        "counts": {
            **counts,
            "total_rows": len(artifacts),
        },
        "broken": broken_entries,
        "unlinked": unlinked_entries,
        "orphaned": orphaned_entries,
        "divergence": counts["broken"] + counts["unlinked"] + counts["orphaned"],
    }
    return report


def write_divergence_report(report: dict[str, Any], out_dir: Path, *, stamp: str) -> Path:
    """Persist a divergence report as JSON under out_dir/divergence/.

    `stamp` is a caller-supplied filename token (a timestamp string) — core is
    forbidden from reading the clock itself, so the caller stamps the run. Also
    refreshes divergence-latest.json for a stable "current state" pointer.
    """
    div_dir = out_dir / "divergence"
    div_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, default=str) + "\n"
    dated = div_dir / f"divergence-{stamp}.json"
    dated.write_text(payload, encoding="utf-8")
    (div_dir / "divergence-latest.json").write_text(payload, encoding="utf-8")
    return dated
