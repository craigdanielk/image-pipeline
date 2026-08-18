#!/usr/bin/env python3
"""PURE CORE — compile job-v1 to compile/, then execute via graph runtime.

    job-v1 → compile/ (manifest + per-stage JSON)
           → runtime bundle → graph_runtime (execute only)

Usage:
  python core/run_job.py jobs/<name>.job.json --dry-run
  python core/run_job.py jobs/<name>.job.json
  python core/compile_job.py jobs/<name>.job.json --compile-dir <dir>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapters  # noqa: E402
import compile_bundle  # noqa: E402
import job_compiler  # noqa: E402
import jobspec  # noqa: E402
import run_graph  # noqa: E402
import tracking  # noqa: E402


def build_prompt(job):
    """Return compiled ProviderPrompt positive string (compile-only helper)."""
    artifacts = job_compiler.compile_job(job, dry_run=True)
    return artifacts["provider_prompt"]["positive"]


def run(job, dry_run=True, track_row_id=None, compile_dir=None):
    """Compile to compile/ then execute one JobSpec via graph runtime."""
    jobspec.validate(job)
    target_dir = compile_dir or compile_bundle.default_compile_dir(
        job.get("_dir", "."), job["id"]
    )

    source_path = job.get("_source_path")
    if source_path:
        job_compiler.compile_path_to_dir(source_path, target_dir, dry_run=dry_run)
    else:
        bundle = job_compiler.compile_job(job, dry_run=dry_run)
        compile_bundle.write_compile_dir(
            target_dir,
            bundle,
            source={"kind": "job-v1", "path": job.get("_dir", ".")},
        )

    runtime_bundle = compile_bundle.load_runtime_bundle(target_dir)
    plan = runtime_bundle["execution_plan"]
    prompt = (plan.get("prompt") or {}).get("positive", "")
    out = jobspec.output(job)

    tracking.prompt_built(track_row_id, prompt)

    print(f"\n== job {job['id']}  engine={plan['provider']}:{plan['model']}  "
          f"imgtype={out.get('imgtype')}  count={out.get('count')}")
    refs = runtime_bundle["execution_request"].get("reference")
    print(f"   reference: {refs or '(none — text-to-image)'}")
    print(f"   compile_dir: {target_dir}")
    print(f"   compile_hash: {runtime_bundle.get('compile_hash')}")
    print("   --- resolved prompt ---")
    print("   " + prompt)
    print("   -----------------------")

    if not dry_run:
        tracking.generation_started(track_row_id)

    try:
        graph_result = run_graph.run_graph(
            runtime_bundle["graph"],
            runtime_bundle=runtime_bundle,
            compile_dir=target_dir,
            track=False,
        )
    except Exception as e:
        if not dry_run:
            tracking.generation_failed(track_row_id, e)
        raise

    if dry_run:
        cost = graph_result.get("cost")
        print(f"   DRY RUN cost estimate: {json.dumps(cost)[:400]}")
        tracking.cost_estimated(track_row_id, cost)
        return {
            "id": job["id"],
            "dry_run": True,
            "prompt": prompt,
            "cost": cost,
            "compile_hash": runtime_bundle.get("compile_hash"),
            "compile_dir": target_dir,
            "execution_plan": plan,
        }

    saved = graph_result.get("saved") or []
    if saved:
        results = [{"result_url": f"file://{p}"} for p in saved]
        output_ids_by_url = tracking.generated(track_row_id, results)
        for local_path in saved:
            tracking.output_persisted(
                track_row_id, output_ids_by_url, f"file://{local_path}", local_path
            )

    return {
        "id": job["id"],
        "dry_run": False,
        "prompt": prompt,
        "saved": saved,
        "compile_hash": runtime_bundle.get("compile_hash"),
        "compile_dir": target_dir,
    }


def _run_one(job_path, intake="cli", dry_run=True, track=False, product_slug=None):
    try:
        job = adapters.get_intake(intake).load(job_path)
    except (json.JSONDecodeError, OSError) as e:
        tracking.job_intake_failed(job_path, e)
        raise

    job["_source_path"] = os.path.abspath(job_path)
    track_row_id = tracking.job_created(job, product_slug=product_slug)
    tracking.status(track_row_id, "validated")

    try:
        result = run(job, dry_run=dry_run, track_row_id=track_row_id)
    except Exception as e:
        tracking.status(track_row_id, "failed", error=str(e)[:2000])
        raise
    return result


# AURELIX-CAPABILITY — this file declares a capability under `--describe`.
#
# image-pipeline is a SEPARATE repository from aurelix-ag; no import may reach
# it. The dict below is a literal, validated by `lib.capability.validate` when
# the register is compiled. This is the instrument that SPENDS MONEY.
CAPABILITY = {
    "id": "aurelix.generator.image-pipeline",
    "name": "Creative compiler runner — compile a job-v1 and execute it through the provider",
    "kind": "generator",
    "invocation": "python3 core/run_job.py <job>.job.json [--dry-run] [--concurrency N] [--track]",
    "preconditions": [
        "python3 3.9+ (core is dep-free)",
        "the `higgsfield` CLI on PATH — present at ~/.npm-global/bin/higgsfield on this machine, 2026-08-18",
        "an AUTHENTICATED higgsfield session. The credential is that CLI's own login, NOT an env var: there is "
        "deliberately no API key anywhere in this repo, and .env.example carries only SUPABASE_* plus a note "
        "that the AI drafting path uses the local `claude` CLI subscription",
        "a Higgsfield credit balance for a real run. --dry-run needs no credits but DOES need the same "
        "authenticated session, because it shells to `higgsfield generate cost <model> --prompt ... --json`",
        "--track additionally needs .env with SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY",
    ],
    "inputs": [
        "one or more job-v1 .job.json paths (or '-' for stdin, alone)",
        "the compile/ bundle it produces itself from each job",
        "reference images the job names, uploaded to the provider on a real run",
    ],
    "outputs": [
        "compile/<job-id>/ — the full artifact directory (written on dry-run too)",
        "generated images written by the output_local adapter to the job's output dir",
        "stdout: engine/model, reference list, compile_dir, compile_hash, the resolved prompt, and either "
        "the DRY RUN cost estimate JSON or the saved file paths",
        "with --track, rows mirrored into Supabase per db/SCHEMA.md",
    ],
    "outcome": "actual generated media for a job spec — or, with --dry-run, the provider's own priced cost "
               "estimate for that exact resolved prompt",
    "exit_contract": {
        "0": "every job compiled and executed (or was priced) without raising",
        "1": "SystemExit from a failed job in the fan-out ('N of M job(s) failed'), from combining '-' with "
             "other paths, or any uncaught exception — a provider RuntimeError, a non-completed generation, "
             "an unreadable job file",
        "2": "argparse usage error (no positional job path, unknown flag)",
    },
    "measures": [
        "the provider's real priced cost for the resolved prompt, under --dry-run",
        "the compile_hash, so an identical job is recognisably the same work",
        "whether the provider actually completed: a job returning without status=='completed' or without a "
        "result_url raises rather than being recorded as done",
    ],
    "cannot_see": [
        "whether the image is any good — on-model, on-brand, usable, or the shot that was asked for. There is "
        "no visual gate anywhere in this repo; the operator UI's approve/reject gallery is a human deciding",
        "whether --dry-run is free of side effects at the provider. It is free of CREDITS, but it is NOT "
        "offline: `cost()` shells to `higgsfield generate cost ... --json`, a live authenticated call to a "
        "paid provider's API. Treat it as a network operation against a billed account, not as a local estimate",
        "the credit balance. Nothing checks it before spending; a run fails at the provider rather than "
        "refusing up front",
        "cape-crypto. Measured 2026-08-18: 76 .job.json in this repo, 85 across tenants/, ZERO cape-crypto — "
        "and there never will be one on disk, because aurelix-ag's commission-media.py writes the spec to a "
        "TemporaryDirectory before shelling here. The intended tenant has no persisted job library, so nothing "
        "about Cape Crypto's imagery is reviewable, re-runnable or diffable from this repo",
        "the Aurelix budget. commission-media.py owns the cache and the spend decision; this runner generates "
        "whatever spec it is handed, so a caller with no budget check spends without being told no",
        "Remotion motion jobs. A media_type=video job with motion.engine=='remotion' is rendered by "
        "aurelix-ag/web-builder/motion/, never here, so 'the pipeline ran' does not mean the motion did",
        "whether the generated file ever reaches a site. output_local writes to disk; there is no CDN, Drive "
        "or webhook adapter (CLAUDE.md known gap 3), so delivery is a manual step this instrument cannot report on",
    ],
    "reachable_from": [
        "aurelix-ag/web-builder/scripts/quality/commission-media.py:_run_pipeline — subprocess "
        "[sys.executable, $IMAGE_PIPELINE_ROOT/core/run_job.py, <tmp job file>] (+ --dry-run), "
        "cwd=IMAGE_PIPELINE_ROOT. This is the ONLY executable call site measured anywhere",
        "README.md:26/:99, WORKFLOW.md:46-48, SKILL.md, ADAPTERS.md — documentation, not call sites",
        "NOT reached from ui/server.py: the UI shells to compile_job.py and run_graph.py, never to this file",
    ],
    "cost": "MONEY. A real run bills Higgsfield credits per generated image against the CLI's authenticated "
            "account; there is no ceiling in this repo and no balance check. --dry-run costs no credits but "
            "still makes a live authenticated `higgsfield generate cost` call. NOT_RUN in this survey for that "
            "reason: exercising it would require `higgsfield` to be logged in and would hit a billed provider's "
            "API, so only --describe was executed.",
}


def main(argv=None):
    if "--describe" in (sys.argv[1:] if argv is None else list(argv)):
        print(json.dumps(CAPABILITY, indent=2))
        raise SystemExit(0)
    ap = argparse.ArgumentParser(description="Compile and run image-pipeline JobSpecs.")
    ap.add_argument("job", nargs="+", help="path(s) to .job.json files (or '-' for stdin, alone only)")
    ap.add_argument("--dry-run", action="store_true", help="cost estimate only; no credits, no files")
    ap.add_argument("--intake", default="cli", help="intake adapter (default: cli)")
    ap.add_argument("--track", action="store_true",
                     help="mirror progress into Supabase (db/SCHEMA.md); off by default, needs .env")
    ap.add_argument("--product-slug", default=None,
                     help="get-or-create a products row and link this job to it (tracking only)")
    ap.add_argument("--concurrency", type=int, default=8,
                     help="max parallel workers when multiple job paths are given (default 8)")
    a = ap.parse_args(argv)

    if a.track:
        os.environ["IMAGE_PIPELINE_TRACK"] = "1"

    if len(a.job) > 1 and "-" in a.job:
        raise SystemExit("stdin ('-') can't be combined with other job paths — stdin is only readable once")

    if len(a.job) == 1:
        result = _run_one(a.job[0], intake=a.intake, dry_run=a.dry_run, track=a.track,
                           product_slug=a.product_slug)
        if result.get("saved"):
            print("\nDONE:")
            for p in result["saved"]:
                print("  " + p)
        return result

    max_workers = max(1, min(a.concurrency, len(a.job)))
    print(f"\n== fanning out {len(a.job)} job(s) across {max_workers} worker(s) ==")

    results = [None] * len(a.job)
    errors = [None] * len(a.job)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_idx = {
            pool.submit(_run_one, path, a.intake, a.dry_run, a.track, a.product_slug): i
            for i, path in enumerate(a.job)
        }
        for future in as_completed(future_to_idx):
            i = future_to_idx[future]
            path = a.job[i]
            try:
                results[i] = future.result()
                print(f"  [ok]   {path}")
            except Exception as e:
                errors[i] = e
                print(f"  [FAIL] {path}: {e}")

    ok_count = sum(1 for r in results if r is not None)
    print(f"\n== {ok_count}/{len(a.job)} job(s) succeeded ==")
    for r in results:
        if r and r.get("saved"):
            for p in r["saved"]:
                print("  " + p)

    if any(errors):
        raise SystemExit(f"{sum(1 for e in errors if e)} of {len(a.job)} job(s) failed — see [FAIL] lines above")

    return results


if __name__ == "__main__":
    main()
