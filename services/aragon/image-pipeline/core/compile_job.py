#!/usr/bin/env python3
"""Compile job-v1 source to compile/ artifact directory.

The runtime never compiles. Use this CLI to inspect or persist compile output
before execution via run_graph.py.

Usage:
  python core/compile_job.py jobs/foo.job.json --compile-dir ./compile/foo
  python core/compile_job.py jobs/foo.job.json -o foo.compile.json  # legacy single blob
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import compile_bundle  # noqa: E402
import job_compiler  # noqa: E402


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description="Compile job-v1 to compile/ artifact directory.")
    ap.add_argument("job", help="path to .job.json")
    ap.add_argument(
        "--compile-dir",
        help="write compile/ directory (manifest + per-stage JSON files)",
    )
    ap.add_argument("-o", "--output", help="legacy: write single artifacts JSON blob")
    ap.add_argument("--dry-run", action="store_true", help="mark execution plan as cost_estimate")
    a = ap.parse_args(argv)

    if a.compile_dir:
        manifest = job_compiler.compile_path_to_dir(
            a.job, a.compile_dir, dry_run=a.dry_run
        )
        print(f"Wrote compile directory: {os.path.abspath(a.compile_dir)}")
        print(f"  manifest_hash: {manifest.get('manifest_hash')}")
        print(f"  compile_hash:    {manifest.get('compile_hash')}")
        return {"manifest": manifest, "compile_dir": a.compile_dir}

    artifacts = job_compiler.compile_path(a.job, dry_run=a.dry_run)
    payload = json.dumps(artifacts, indent=2, ensure_ascii=False)
    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            f.write(payload)
            f.write("\n")
        print(f"Wrote {a.output}")
    else:
        print(payload)
    return artifacts


if __name__ == "__main__":
    main()
