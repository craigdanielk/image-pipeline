# Phase 0 Fork Consolidation — graph-native creative orchestration engine

BRIEF #29790 · attempt-2 · tenant `00000000-0000-0000-0000-000000000001`

## Status: COMPLETED

Architecture spec §17 "Phase 0 fork consolidation" is **completed** by this
brief. The canonical graph-native engine now lives at a single home:

    services/aragon/image-pipeline/

## What consolidated

Before this brief there were two divergent lineages of the graph engine:

| Lineage | Location | State |
|---------|----------|-------|
| **canonical** (working) | `core/` (repo root) | Full graph engine: compiler, IR, frontier scheduler, runtime, persistence, reactflow export, tested end-to-end |
| **tenant fork** (partial) | `services/aragon/image-pipeline/core/` | attempt-1 Phase-1 stubs only: typed `node_registry.py` + `graph_ir.py`, no runtime |

The tenant fork was a design sketch (Phase-1 typed-port stubs, explicitly
"no runtime execution"). The canonical `core/` lineage is the one that
compiles job-v1 → graph-v1, executes deterministically, and passes the
regression suite. Consolidation therefore **promotes the canonical lineage
into the service home** and retires the partial fork.

### Consolidated modules (`services/aragon/image-pipeline/`)

- `core/graph_compiler.py` — job-v1 → graph-v1 DAG lowering
- `core/graph_ir.py` — loader, `validate()`, DAG cycle detection, port typing, `GraphFrontier`, `slice_to_target`
- `core/run_graph.py` — runtime entrypoint (frontier scheduler, `--dry-run`, `--track`, `--node`, `--parent-run-id`, idempotency)
- `core/graph_runtime.py` — per-node handlers wired to unchanged adapters
- `core/node_registry.py` — node type registry
- `core/execution_state.py` — **new**: per-node state machine, disk checkpoints, Supabase mirror with `parent_job_id` lineage (deterministic replay / resume)
- `core/graph_persistence.py` — `graph_runs` / `graph_node_runs` tracking layer
- `core/run_job.py` + full compile chain (`job_compiler`, `compile_bundle`, `jobspec`, `prompt_compiler`, …) — legacy job-v1 runner preserved unchanged
- `core/adapters/` — `engine_higgsfield`, `reference_local`, `output_local`, `intake_cli` — **interface-unchanged**
- `schemas/graph-v1.schema.json` + published node/example schemas
- `tools/graph_to_reactflow.py` — graph-v1 JSON → React Flow node/edge diagram
- `tests/` — graph + node + job compiler regression suite (47 passing)
- `jobs/` — job-v1 fixtures incl. `starz-kid-images`
- `ADAPTERS.md` — frozen adapter contracts

## Baselines

- **Canonical baseline**: repo-root `core/` at brief start (working graph engine, HEAD `44d23f3`).
- **Tenant-fork baseline**: `services/aragon/image-pipeline/core/{node_registry,graph_ir}.py` attempt-1 Phase-1 stubs — superseded by the canonical modules of the same name.

The repo-root `core/` tree is intentionally left in place (unmodified) as the
provider/tenant-agnostic source; the service directory is the consolidated
deployable home. A follow-up may redirect root `core/` to import from the
service home once all consumers (UI backend) are repointed — that redirect is
**deferred** and out of scope for this brief (no consumer currently imports
from the service path).

## Design-time / runtime separation (enforced)

No graph runtime node (`graph_runtime.py` handlers) invokes an LLM. LLM usage
is confined to design-time authoring (`ui/brief_drafter.py`, spec extraction).
`graph_runtime.NODE_TYPES` = `artifacts.load`, `reference.resolve`,
`engine.generate`, `output.persist`, `result.as_ref` — all deterministic,
provider-adapter-mediated, zero model calls.

## Verification

- `python3 -m pytest tests/` → 47 passed
- `python3 core/run_graph.py --dry-run jobs/starz-kid-images/kid-hero.job.json` → exit 0
- graph_compiler emits `schema_version: graph-v1`, references `job-v1` input
- `execution_state` round-trips checkpoints and enforces legal transitions
- reactflow export renders any graph-v1 JSON to node/edge diagram
