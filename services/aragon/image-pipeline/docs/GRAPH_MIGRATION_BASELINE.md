# Graph Migration Baseline (Phase 1 Freeze)

**Status:** FROZEN — backwards-compatibility baseline for graph-v1 work.
**Freeze date:** 2026-07-07 (BRIEF #29797 — graph-orchestrator-phase1-freeze-jobspec)
**Recorded:** 2026-07-11

## Freeze commit SHA (job-v1 runner)

| Anchor | SHA | Meaning |
|--------|-----|---------|
| Adapter-contract freeze HEAD | `e2ba9e4e00d4ca6ee7c1ff68f4a179994446fe42` | Tree state at which `ADAPTERS.md` + this baseline doc were captured. |
| `core/run_job.py` last-modified | `5c36fb3e54fd60792b9330ba5c5d14577e927596` | The commit that last touched the job-v1 runner (`feat(aragon): consolidate graph-native engine into services/aragon/image-pipeline`). No further runtime changes permitted under freeze. |

Any graph-v1 (`run_graph.py`) work MUST branch from these anchors and MUST NOT
alter the four adapter signatures or the `run_job.py` node order documented
below. Provider swaps are additive registry entries, never core edits.

## Compatibility boundary

The authoritative Phase-1 compatibility boundary is the four adapter seams in
[`../ADAPTERS.md`](../ADAPTERS.md):

1. `intake_cli.load(source) -> dict`
2. `reference_local.resolve(reference, job_dir) -> str | None`
3. `engine_higgsfield.cost(...)` / `engine_higgsfield.generate(...) -> list[dict]`
4. `output_local.persist(results, job, out_dir) -> list[str]`

No graph code may merge before this freeze doc lands (definition-of-done gate).

## `core/run_job.py` five-node sequence (unchanged)

The linear job-v1 runner is the graph-v1 linear-compile target. Its node order
is frozen:

```
1. intake.load(path)          # intake_cli adapter
2. jobspec.validate(job)      # core, in-runner
   → build_prompt(job)        # core, in-runner
3. reference.resolve(ref)     # reference_local adapter
4. engine.cost | engine.generate(...)   # engine_higgsfield adapter
5. output.persist(...)        # output_local adapter
```

`tracking.py` mirroring is optional (`--track`) and is NOT a node/adapter seam.

## Tenant fork diff — multi-reference array extension

The tenant fork extends `reference` from a single str/dict to a **list** of
str/dict items at **compile time**:

- **Compiler input requirement:** consumers may pass `reference` as
  `list[str | dict]`. The graph compiler lowers this to `image_path_list` ports;
  the engine adapter maps a `list[str]` reference to multiple `--image` flags
  (canonical since 2026-07-07).
- **Runtime floor unchanged:** the canonical adapter signatures in `ADAPTERS.md`
  (§2 Reference, §3 Engine) remain the runtime contract. The multi-reference
  array is an additive compile-time input extension, not a signature change, so
  it stays within the freeze.

See `ADAPTERS.md` §2 and §3 "Consumer extension (compile-time)" notes.

## Out of scope for Phase 1

Execution scheduler · `run_graph.py` runtime · React Flow UI · Supabase
persistence. These are graph-v1 concerns that must not regress the frozen
job-v1 baseline above.
