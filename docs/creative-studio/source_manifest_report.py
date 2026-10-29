#!/usr/bin/env python3
"""Read-only source evidence check. Does not import or execute the product."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], stderr=subprocess.DEVNULL
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("source-manifest.json"))
    parser.add_argument("--source-root", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    root = (args.source_root or Path(manifest["local_path"])).resolve()
    baseline = manifest["head"]
    failures = []
    nested_count = 0
    changed_count = 0
    for row in manifest["files"]:
        relative = Path(row["path"])
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root):
            failures.append({"path": row["path"], "reason": "invalid relative path"})
            continue
        try:
            actual = digest(path.read_bytes())
            if actual != row["working_sha256"]:
                failures.append({"path": row["path"], "reason": "working source changed"})
            recorded = digest(git(root, "show", f"{baseline}:{relative.as_posix()}"))
            if recorded != row["head_sha256"]:
                failures.append({"path": row["path"], "reason": "baseline hash mismatch"})
            if (actual == recorded) != row["working_matches_head"]:
                failures.append({"path": row["path"], "reason": "working/baseline comparison changed"})
            changed_count += actual != recorded
            nested = root / "services/aragon/image-pipeline" / relative
            if nested.is_file() != row["nested_present"]:
                failures.append({"path": row["path"], "reason": "historical counterpart presence changed"})
            elif nested.is_file():
                nested_count += 1
                if (digest(nested.read_bytes()) == actual) != row["nested_matches_working"]:
                    failures.append({"path": row["path"], "reason": "historical counterpart comparison changed"})
        except (OSError, subprocess.CalledProcessError, KeyError):
            failures.append({"path": row["path"], "reason": "source evidence unavailable"})
    ancestry = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", baseline, "HEAD"],
        capture_output=True,
    )
    if ancestry.returncode:
        failures.append({"path": ".git", "reason": "recorded baseline is not an ancestor of current HEAD"})
    print(json.dumps({
        "kind": "source_evidence_integrity",
        "product_testing": False,
        "provider_calls": 0,
        "repository": manifest["repository"],
        "baseline": baseline,
        "files_checked": len(manifest["files"]),
        "historical_counterparts": nested_count,
        "working_files_different_from_baseline": changed_count,
        "status": "consistent" if not failures else "drift_detected",
        "failures": failures,
    }, indent=2))
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
