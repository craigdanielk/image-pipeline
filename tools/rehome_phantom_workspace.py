#!/usr/bin/env python3
"""Identify (and optionally re-home) writes that landed in the phantom
``workspace/default`` scope before write endpoints failed closed on tenant.

Background
----------
Before the fail-closed fix, any write endpoint called without a tenant param
resolved to a phantom ``default`` workspace (``workspace/default/``). Review
state — artifact registry rows (comments, approvals, soft-deletes) and asset
approvals — silently landed there and was invisible under every real tenant.

This tool scans ``<root>/workspace/default`` for such orphaned rows, COUNTS
them, and either:
  * reports them (default, read-only), or
  * re-homes them into a target tenant workspace (``--rehome <tenant>``).

Rows that cannot be safely re-homed (an id collision with a DIFFERENT row
already present under the target) are left in place and listed as an explicit
"unrecoverable" report — nothing is silently dropped or overwritten.

Usage
-----
    python3 tools/rehome_phantom_workspace.py                 # report only
    python3 tools/rehome_phantom_workspace.py --root .        # explicit root
    python3 tools/rehome_phantom_workspace.py --rehome joga   # re-home to joga
    python3 tools/rehome_phantom_workspace.py --json          # machine output

Exit code is 0 when there is nothing phantom to do, 0 after a successful
re-home, and 2 when phantom rows exist but no --rehome target was given
(so CI can flag the drift).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PHANTOM = "default"
REGISTRY = "artifact_registry.json"
APPROVALS = "asset_approvals.json"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def scan(root: Path) -> dict:
    """Count phantom rows without mutating anything."""
    wdir = root / "workspace" / PHANTOM
    reg = _load(wdir / REGISTRY)
    appr = _load(wdir / APPROVALS)
    artifacts = reg.get("artifacts", {}) if isinstance(reg.get("artifacts"), dict) else {}
    approvals = appr if isinstance(appr, dict) else {}
    return {
        "phantom_dir": str(wdir),
        "exists": wdir.is_dir(),
        "artifact_rows": len(artifacts),
        "approval_rows": len(approvals),
        "artifact_ids": sorted(artifacts.keys()),
        "approval_paths": sorted(approvals.keys()),
    }


def rehome(root: Path, target: str) -> dict:
    """Move phantom rows into ``workspace/<target>``. Non-destructive: an id
    that already exists under the target with DIFFERENT content is reported as
    unrecoverable and left in the phantom scope."""
    src = root / "workspace" / PHANTOM
    dst = root / "workspace" / target
    dst.mkdir(parents=True, exist_ok=True)

    moved = {"artifacts": 0, "approvals": 0}
    unrecoverable: list[dict] = []

    # --- artifact registry rows -----------------------------------------
    src_reg = _load(src / REGISTRY)
    src_arts = src_reg.get("artifacts", {}) if isinstance(src_reg.get("artifacts"), dict) else {}
    if src_arts:
        dst_reg = _load(dst / REGISTRY)
        if not isinstance(dst_reg.get("artifacts"), dict):
            dst_reg = {"version": src_reg.get("version", 1), "artifacts": {}}
        dst_arts = dst_reg["artifacts"]
        remaining: dict = {}
        for aid, row in src_arts.items():
            if aid in dst_arts and dst_arts[aid] != row:
                unrecoverable.append({"kind": "artifact", "id": aid,
                                      "reason": "id collision with differing row under target"})
                remaining[aid] = row
                continue
            dst_arts[aid] = row
            moved["artifacts"] += 1
        dst_reg["artifacts"] = dst_arts
        (dst / REGISTRY).write_text(json.dumps(dst_reg, indent=2) + "\n", encoding="utf-8")
        src_reg["artifacts"] = remaining
        (src / REGISTRY).write_text(json.dumps(src_reg, indent=2) + "\n", encoding="utf-8")

    # --- asset approvals -------------------------------------------------
    src_appr = _load(src / APPROVALS)
    if isinstance(src_appr, dict) and src_appr:
        dst_appr = _load(dst / APPROVALS)
        if not isinstance(dst_appr, dict):
            dst_appr = {}
        remaining_appr: dict = {}
        for path, status in src_appr.items():
            if path in dst_appr and dst_appr[path] != status:
                unrecoverable.append({"kind": "approval", "id": path,
                                      "reason": "path collision with differing status under target"})
                remaining_appr[path] = status
                continue
            dst_appr[path] = status
            moved["approvals"] += 1
        (dst / APPROVALS).write_text(json.dumps(dst_appr, indent=2) + "\n", encoding="utf-8")
        (src / APPROVALS).write_text(json.dumps(remaining_appr, indent=2) + "\n", encoding="utf-8")

    return {"target": target, "moved": moved, "unrecoverable": unrecoverable}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".", help="repo root containing workspace/ (default: .)")
    ap.add_argument("--rehome", metavar="TENANT", help="re-home phantom rows into this tenant workspace")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    report = scan(root)

    if args.rehome and (report["artifact_rows"] or report["approval_rows"]):
        report["rehome"] = rehome(root, args.rehome)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"phantom workspace: {report['phantom_dir']}")
        print(f"  exists          : {report['exists']}")
        print(f"  artifact rows   : {report['artifact_rows']}")
        print(f"  approval rows   : {report['approval_rows']}")
        if report.get("rehome"):
            rh = report["rehome"]
            print(f"  re-homed -> {rh['target']}: {rh['moved']}")
            if rh["unrecoverable"]:
                print(f"  UNRECOVERABLE ({len(rh['unrecoverable'])}):")
                for u in rh["unrecoverable"]:
                    print(f"    - {u['kind']} {u['id']}: {u['reason']}")
        elif report["artifact_rows"] or report["approval_rows"]:
            print("  (report-only; pass --rehome <tenant> to re-home)")

    total = report["artifact_rows"] + report["approval_rows"]
    if total == 0:
        return 0
    if args.rehome:
        return 0
    return 2  # drift exists, no action taken


if __name__ == "__main__":
    sys.exit(main())
