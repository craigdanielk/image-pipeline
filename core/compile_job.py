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


# AURELIX-CAPABILITY — this file declares a capability under `--describe`.
#
# image-pipeline is a SEPARATE repository from aurelix-ag, so this declaration
# may not `from lib.capability import describe` — the import would not resolve
# for anyone else. The dict below is a literal, validated by the compiler
# through `lib.capability.validate` when the register is compiled: same schema,
# same rules, `cannot_see` not optional.
CAPABILITY = {
    "id": "aurelix.compiler.image-job",
    "name": "Creative compiler — job-v1 source to compile/ artifact directory",
    "kind": "compiler",
    "invocation": "python3 core/compile_job.py <job>.job.json --compile-dir <dir>",
    "preconditions": [
        "python3 3.9+ — the core is dep-free by design; no venv, no requirements",
        "a job-v1 .job.json that validates against core/job.schema.json",
        "any cad.md, ProductSpec and reference images the job names must be resolvable relative to the job file",
    ],
    "inputs": [
        "one job-v1 .job.json path",
        "the job's sibling cad.md / knowledge-store ProductSpec, if it names one",
    ],
    "outputs": [
        "<compile-dir>/ — manifest.json, ProductSpec.json, CreativeSpec.json, EditSpecs.json, "
        "ExecutionRequest.json, ProviderPrompt.json, ProviderExecutionPlan.json, graph.json, RuntimeContext.json",
        "or, with -o, a single legacy artifacts JSON blob; with neither, the blob on stdout",
        "stdout: manifest_hash and compile_hash",
    ],
    "outcome": "the fully lowered, inspectable creative plan for a job — every stage as its own JSON file — "
               "without contacting any provider. This is the compile half of 'the runtime never compiles, "
               "the compiler never executes'",
    "exit_contract": {
        "0": "compiled; the compile directory (or blob) was written",
        "1": "any uncaught exception — schema validation failure, unresolvable reference, unreadable job file",
        "2": "argparse usage error (missing the positional job path, unknown flag)",
    },
    "measures": [
        "that a job-v1 source lowers completely: every stage artifact is produced or the compile fails",
        "compile determinism by hash — the same job-v1 compiled twice yields identical per-file hashes in manifest.json",
        "the resolved provider prompt and ProviderExecutionPlan, inspectable before a single credit is spent",
    ],
    "cannot_see": [
        "anything about the image. It never executes, never calls a provider and never looks at a pixel — "
        "a compile that succeeds says nothing about whether the render is on-model, on-brand or usable",
        "cost. --dry-run here only MARKS the execution plan as method=cost_estimate; the priced number comes "
        "from the runtime calling `higgsfield generate cost`, not from this file",
        "whether the named engine/model still exists at the provider, or whether the higgsfield CLI is "
        "installed or authenticated — none of that is touched at compile time",
        "whether reference images are the right ones. It resolves paths; it cannot judge a plate",
        "any Aurelix tenant, from this CLI. Measured 2026-08-18: 76 .job.json in this repo, 85 more across "
        "tenants/, ZERO cape-crypto. Aurelix does not put its jobs on disk here — "
        "aurelix-ag/web-builder/scripts/quality/commission-media.py lowers a gap via lib/image_jobs.to_job_v1, "
        "writes the spec to a TemporaryDirectory and shells straight to core/run_job.py. So a cape-crypto job "
        "leaves no trace in jobs/ and this CLI has never been on that path",
        "the Aurelix job_hash. aurelix-ag/web-builder/lib/image_jobs.py hashes the palette into its own cache "
        "key; the compile_hash written here is a different identity over a different object, and nothing "
        "reconciles the two",
    ],
    "reachable_from": [
        "ui/server.py:1260, :1303, :1520, :1558 — the operator UI shells out via _run_core('compile_job.py', ...)",
        "README.md:29 quickstart (documentation, not a call site)",
        "NOT reached from aurelix-ag: measured 2026-08-18, zero references to image-pipeline in that repo's code",
    ],
    "cost": "~1s per job, fully offline. No network, no credentials, no provider, no credits, no money.",
}


def main(argv: list[str] | None = None) -> dict:
    if "--describe" in (sys.argv[1:] if argv is None else list(argv)):
        print(json.dumps(CAPABILITY, indent=2))
        raise SystemExit(0)
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
