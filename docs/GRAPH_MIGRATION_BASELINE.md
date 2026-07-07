# Graph Migration Baseline (job-v1 Freeze)

**Frozen at:** 2026-07-07  
**Canonical repo:** `services/aragon/image-pipeline` (within `craigdanielk/ARAGON`)  
**Parent brief:** `BRIEF::graph-native-creative-orchestration-engine::2026-07-07` (#29789)  
**Phase 1 child:** `BRIEF::graph-orchestrator-phase1-freeze-jobspec::2026-07-07` (#29792)

## Freeze commit

| Field | Value |
|-------|-------|
| **Branch** | `main` |
| **SHA** | `e2a93649df9b8add2f3a2f88855c27c445d768b7` |
| **Date** | 2026-07-05 04:48:47 +0200 |
| **Message** | `feat(image-pipeline): fan-out multi-job execution via ThreadPoolExecutor` |
| **Docs commit** | `b9b6ad0e6f6c429a5246803e9b2fc066612fb265` — ADAPTERS.md + this file (adapter code unchanged) |

This SHA is the **compatibility baseline** for graph-v1 work. Adapter contracts are documented in [`ADAPTERS.md`](../ADAPTERS.md) at this commit. The five-node `core/run_job.py` pipeline MUST remain semantically equivalent for all existing `job-v1` files until the graph compiler proves parity.

## What is frozen

| Component | Path | Notes |
|-----------|------|-------|
| JobSpec schema | `core/job.schema.json`, `core/jobspec.py` | `schema_version: "job-v1"` |
| Runner sequence | `core/run_job.py` | intake → validate → prompt → reference → engine → persist |
| Intake adapter | `core/adapters/intake_cli.py` | `load(source)` |
| Reference adapter | `core/adapters/reference_local.py` | `resolve(reference, job_dir)` — **single ref only in canonical** |
| Engine adapter | `core/adapters/engine_higgsfield.py` | `cost(...)`, `generate(...)` |
| Output adapter | `core/adapters/output_local.py` | `persist(results, job, out_dir)` |
| Adapter registry | `core/adapters/__init__.py` | `get_intake`, `get_reference`, `get_engine`, `get_output` |

## What is explicitly NOT frozen (future graph work)

- `graph-v1` schema, `graph_ir.py`, `graph_compiler.py`, `node_registry.py`, `run_graph.py`
- React Flow export tooling
- Supabase tracking DDL application to shared project

## Consumer deployments (outside canonical core)

The canonical service is **tenant-agnostic**. Brand-specific job libraries live in
**consumer workspaces** (e.g. `tenants/<slug>/tools/image-pipeline/`) and invoke
the canonical runner by path — they do not ship a duplicate `core/`.

```bash
# Consumer jobs, canonical engine (no forked core required)
python3 services/aragon/image-pipeline/core/run_job.py \
  tenants/<slug>/tools/image-pipeline/jobs/<campaign>/<job>.job.json --dry-run
```

### Multi-reference arrays

Canonical `job-v1` accepts `reference` as a **list** of paths/objects. The
reference adapter resolves each item; the engine adapter fans out multiple
`--image` flags. No tenant-specific code paths.

### input_images URL handling

Canonical `engine_higgsfield` downloads HTTP URLs then uploads via `higgsfield upload create`.
Consumer-specific URL-object shortcuts are **not** in canonical — graph compiler normalizes
to the canonical upload path.

### Layout

| Path | Role |
|------|------|
| `services/aragon/image-pipeline` | Canonical agnostic service (this repo) |
| `tenants/<slug>/tools/image-pipeline/` | Consumer job library + optional local overrides |

## Verification commands (baseline)

```bash
# From repo root (ARAGON)
cd services/aragon/image-pipeline

# Confirm on main branch
git branch --show-current   # expect main
git merge-base --is-ancestor e2a93649df9b8add2f3a2f88855c27c445d768b7 HEAD && echo "freeze SHA is ancestor of HEAD"

# Dry-run smoke (no credits if --dry-run)
python3 core/run_job.py jobs/primikey-usba/scene-float-a.job.json --dry-run
```

## Design-time / runtime boundary (architectural rule)

> **The runtime may only execute graphs. LLMs may only author, transform, optimize, or validate graphs. They may never participate in graph execution.**

This rule is inherited from parent brief #29789 and applies to all graph-v1 node implementations.
