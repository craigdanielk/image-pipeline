# Graph runtime

Phase 2 execution layer for graph-v1 DAGs. Compiles job-v1 files via `graph_compiler`, executes nodes through `graph_runtime`, optionally persists runs via `graph_persistence`.

## Architecture

```
job.json  →  graph_compiler.compile_path  →  graph-v1 DAG
                                              ↓
                                         run_graph.run_graph
                                              ↓
                              graph_ir.topological_sort (plan)
                                              ↓
                    for each node: graph_runtime.execute_node
                                              ↓
                         adapters (intake / reference / engine / output)
```

### Node executors (`core/graph_runtime.py`)

| Node | Behavior |
|------|----------|
| `intake.load` | Load via `adapters.get_intake` from `params.source`, or fall back to `graph.metadata.job_snapshot` |
| `jobspec.validate` | `jobspec.validate(job)` |
| `prompt.build` | `prompt_compiler.compile_from_job_v1` → positive string; stores full `ProviderPrompt` in ctx |
| `reference.resolve` | `adapters.get_reference`; list refs resolved individually |
| `engine.generate` | `engine.cost` when `dry_run=True`; `engine.generate` otherwise |
| `output.persist` | `adapters.get_output.persist` (skipped on dry run) |

`run_graph.py` matches `run_job.py` semantics for the six-node linear pipeline emitted by `graph_compiler`.

## CLI

```bash
# Compile + dry-run (default cost estimate, no credits)
python3 core/run_graph.py jobs/primikey-usba/scene-float-a.job.json --dry-run

# Live generation
python3 core/run_graph.py jobs/primikey-usba/scene-float-a.job.json

# Optional DB tracking (same env gate as tracking.py)
python3 core/run_graph.py jobs/foo.job.json --dry-run --track \
  --tenant-id 00000000-0000-0000-0000-000000000001 \
  --idempotency-key my-run-2026-07-07
```

## Idempotency contract

When `--idempotency-key` (or `idempotency_key=` kwarg) is set **and** tracking is enabled:

1. Before execution, `graph_persistence.find_idempotent_run(tenant_id, key)` queries `graph_runs` for a **completed** row with matching `(tenant_id, idempotency_key)`.
2. On hit, the stored `result` jsonb is returned immediately — no nodes re-execute, no credits spent.
3. On miss, a new `graph_runs` row is inserted (`status=running`), nodes execute, then `complete_graph_run(status=completed, result=...)`.

Partial unique index: `(tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL`.

Re-runs with the **same key** while a prior run is still `running` or `failed` are **not** short-circuited — only completed runs cache.

## tenant_id usage

- **Nullable everywhere** — `null` means tenant-agnostic (single-operator default).
- Passed through to `graph_runs`, `compile_artifacts`, and (future) `jobs.graph_run_id` bridge.
- Idempotency scope is per `(tenant_id, idempotency_key)` — same key under different tenants is allowed.

## DB tables

See `db/SCHEMA.md` (graph runtime section) and migration `db/migrations/20260707_graph_runtime_persistence.sql`.

| Table | Purpose |
|-------|---------|
| `compile_artifacts` | Content-addressed triple-spec compile cache (`content_hash` unique per tenant) |
| `graph_runs` | One row per graph execution; stores snapshot, idempotency key, result |
| `graph_node_runs` | Per-node status + output snapshot within a run |
| `jobs.*` (bridge) | Added columns: `compile_hash`, `provider_prompt`, `execution_request`, `graph_run_id` |

## Persistence gate

Same as `tracking.py`:

- `IMAGE_PIPELINE_TRACK=1`
- `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` in env or `.env`

Off by default. All writes are best-effort except secret lint (blocks before DB write).

## Not yet applied to Supabase

Migration `20260707_graph_runtime_persistence.sql` is **local only** until run against the linked Supabase project:

```bash
supabase db push   # when ready — verify migration list alignment first
```

Until then, `--track` calls will fail silently (best-effort) against missing tables.

## Compile cache

After `prompt.build`, when tracking is on:

1. `provider_prompt.metadata.content_hash` keys the row.
2. `lookup_compile_cache(tenant_id, hash)` — skip if hit.
3. `store_compile_artifact(...)` — stores ProductSpec, CreativeSpec, ExecutionRequest, ProviderPrompt.

Re-compiling identical specs is idempotent at the content-hash level.

## Testing

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

`tests/test_run_graph.py` covers dry-run e2e (mocked engine), prompt parity with `run_job.build_prompt`, validation failures, and idempotency short-circuit.
