# Execution Engine Rewrite — Design

**Date:** 2026-07-08
**Status:** Approved, pending implementation plan
**Scope:** Backend only. No UI changes in this spec.

## Why

Item 1 of the ranked gap list ("new-product onboarding has no UI path") led to
a design for a Weave-style node authoring canvas. That canvas is worthless
without real, live, per-node execution underneath it. Today's engine
(`core/run_graph.py`) is fully synchronous, whole-graph-only, blocks the
request thread via `subprocess.run`, and has no way to report progress until
the entire graph finishes. This spec rewrites that execution core so a future
canvas (and any other caller) can run a single node, run a subgraph, watch
live per-node status, and have independent branches fail without taking down
unrelated work.

This spec intentionally does **not** touch the node-authoring UI, the
onboarding wizard, or `EditSpec`/operations. Those are follow-on specs that
build on top of this one.

## Current state (baseline, from investigation)

- `core/graph_ir.topological_sort(graph)` (graph_ir.py:140) fully materializes
  a flat execution order via Kahn's algorithm (`_topological_sort`,
  lines 161-178) — no live frontier, no partial/incremental notion.
- `core/graph_runtime.execute_node(node, inputs, ctx)` (line 30) is a plain
  sync dispatcher over `handlers` (`artifacts.load`, `reference.resolve`,
  `engine.generate`, `output.persist`). No async/await anywhere. Each handler
  signature is `(inputs, params, ctx) -> dict[str, Any]` (port → value).
- `core/run_graph.py`'s main loop (lines 139-217) iterates the flat order
  strictly sequentially, one `try/except` around the *entire* loop — any node
  failure aborts every remaining node, including independent siblings.
  `port_outputs` is a plain dict, single-writer, no concurrency.
- `graph_persistence.record_node_run(...)` (lines 117-159) writes per-node
  status to Supabase table `graph_node_runs` via raw PostgREST (`urllib`), on
  a write-only basis — no read-side function exists to fetch current statuses
  for a `graph_run_id`, no Supabase Realtime usage anywhere.
- `ui/server.py`'s `/api/execute` (lines 900-946) is a non-async route that
  shells out via blocking `subprocess.run(..., capture_output=True)` and waits
  for full process exit before responding. No `StreamingResponse`, SSE, or
  WebSocket exists anywhere in the FastAPI app today.
- The actual long-running work — `core/adapters/engine_higgsfield.py`'s
  `execute_plan` → `generate` (line 125) — shells out to the `higgsfield` CLI
  with `--wait --json` (line 135), i.e. deliberately blocks the whole Python
  process for the remote generation's full duration.

## Architecture

### Frontier-based scheduler

`graph_ir.py` gains a `GraphFrontier` class built on the existing
indegree/adjacency bookkeeping (reusing the logic in `_topological_sort`,
graph_ir.py:91-92, 144-178) but exposed as a live, mutable ready-set instead
of a function that drains straight into a flat list:

- `frontier.ready()` — all zero-indegree nodes not yet dispatched.
- `frontier.complete(node_id)` — decrements dependents' indegree, re-evaluates
  readiness, called on successful node completion.
- `frontier.fail(node_id)` — marks `node_id` failed, then transitively marks
  every node reachable from it (not yet completed) as `skipped` without
  dispatching them. Nodes with no path from `node_id` are unaffected and stay
  eligible.

`run_graph.py`'s loop becomes an `asyncio` scheduler:

1. Each tick, take `frontier.ready()`, dispatch every ready node concurrently
   via `asyncio.to_thread(execute_node, node, inputs, ctx)`. `execute_node`
   and all four handlers in `graph_runtime.py` are unchanged — sync, as they
   are today — they're simply called off the event loop thread instead of
   inline.
2. `await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)`.
3. For each completed future: on success, write outputs to `port_outputs`
   (guarded by an `asyncio.Lock` — writes are one-per-node-completion, so
   contention is negligible) and call `frontier.complete(node_id)`; on
   exception, call `frontier.fail(node_id)`.
4. Repeat until the frontier is empty (all nodes completed, failed, or
   skipped).

This makes independent branches (e.g. three sibling plate-generation nodes)
run in parallel instead of one-at-a-time, since nothing serializes dispatch
across the ready set.

### Failure isolation

A node failure no longer aborts the run. Downstream dependents (per
`frontier.fail`) are marked `skipped` and never dispatched. Sibling branches
with no dependency path from the failure keep running to completion. Overall
run status (written via `graph_persistence.complete_graph_run`) becomes:

- `completed` — every node completed.
- `partial` — at least one node completed and at least one failed/skipped.
- `failed` — no node completed (e.g. the failure was at the root, or the only
  node).

### Status streaming

No new external infra (no move to Supabase Realtime). The executor pushes
every status transition into an in-process `asyncio.Queue` keyed by
`graph_run_id`, *in addition to* the existing `graph_persistence.record_node_run`
durable write (unchanged, still the write of record for history/audit).

New routes on `ui/server.py`:

- `POST /api/graph-runs` — body `{compile_dir: str, target_node_id?: str}`.
  Loads `graph.json` from `compile_dir`, optionally slices to only the
  subgraph upstream of (and including) `target_node_id` if provided, starts
  the async executor as a background task (`asyncio.create_task`, replacing
  the current `subprocess.run("run_graph.py", ...)` blocking call for this
  new entry point), returns `{graph_run_id}` immediately — does not block on
  completion.
- `GET /api/graph-runs/{id}` — point-in-time snapshot: overall run status +
  per-node statuses, backed by a **new** `graph_persistence.list_node_runs(graph_run_id)`
  (new function, plain PostgREST GET against `graph_node_runs`, mirrors the
  existing write-side query pattern in `record_node_run`). Needed so a client
  reconnecting mid-run (e.g. page refresh) can resync before subscribing to
  the stream.
- `GET /api/graph-runs/{id}/stream` — `StreamingResponse` with
  `media_type="text/event-stream"`, reads off the in-process queue for that
  `graph_run_id`, emits one SSE event per status transition, closes the
  stream when the run reaches a terminal status (`completed`/`partial`/`failed`).

`/api/execute` and the existing `subprocess.run`-based `_run_core` helper are
**not modified or removed** in this spec — they remain the sync entry point
for existing callers. The new `/api/graph-runs/*` routes are additive.

### Target-node slicing

`run_graph()` gains a new optional parameter `target_node_id: str | None`.
When set, before building the frontier, compute the set of ancestors of
`target_node_id` (reverse-BFS over edges) union `{target_node_id}` itself, and
restrict the graph to that node/edge subset before scheduling. This is what
lets a future caller (e.g. "run just this one node") execute a real subgraph
without re-running unrelated parts of a larger authored graph. Nodes outside
the ancestor set are left entirely untouched — not marked skipped, simply not
part of this run.

## Data model changes

- `graph_persistence.py`: new `list_node_runs(graph_run_id: str) -> list[dict]`
  — GET against `graph_node_runs` filtered by `graph_run_id`, same
  `tracking._rest` urllib client already used for writes. No schema
  migration — reads the same table `record_node_run` already writes.
- `graph_runs.status` gains the `partial` value alongside existing
  `queued|running|completed|failed` (wherever that enum/check is enforced —
  confirm during implementation whether it's a DB check constraint or
  application-level only).
- No changes to `graph-v1` schema (`graph.json` node/edge shape) — the
  scheduler consumes the same structure, just traverses it differently.

## Error handling

- Per-node exceptions inside `execute_node` are caught at the scheduler level
  (around each `asyncio.to_thread` call), not inside `graph_runtime.py` —
  handlers keep raising naturally, same as today.
- A node's exception is recorded via `record_node_run(..., status="failed",
  error=str(exc))` (existing field, already supported) before propagating the
  `frontier.fail()` call.
- If `target_node_id` is supplied but not found in the graph, `run_graph()`
  raises immediately before scheduling anything (fail fast, matches the
  existing validation-then-run structure in `run_graph()` today).
- SSE stream: if the client disconnects mid-run, the run continues
  server-side (background task is independent of the HTTP connection) — a
  reconnect via `GET /api/graph-runs/{id}` then a fresh `/stream` subscribe
  recovers full visibility, no run state is lost.

## Testing plan

- **Unit — `GraphFrontier`**: hand-built diamond graph (A→B, A→C, B→D, C→D).
  Assert `ready()` returns `{B, C}` after `complete(A)`, and `D` is not ready
  until both `complete(B)` and `complete(C)` have been called.
- **Unit — failure isolation**: same diamond graph, `fail(B)` — assert `D` is
  marked `skipped` (never dispatched), `C` still reaches `completed`, overall
  run status is `partial`.
- **Unit — target-node slicing**: graph with an unrelated disconnected
  branch; assert `run_graph(graph, target_node_id=X)` only touches nodes in
  X's ancestor set, leaves the disconnected branch entirely unexecuted (no
  `record_node_run` calls for it).
- **Integration**: a 3-independent-leaf graph (1 root, 3 parallel leaves) run
  with `dry_run=True`; assert wall-clock is closer to
  `max(leaf_durations)` than `sum(leaf_durations)` (proves real concurrency,
  not just non-blocking-but-serial).
- **Regression**: existing `run_graph()` sync entry point and its current
  tests must still pass unmodified — this is additive (new optional param,
  new frontier-based internals), not a breaking signature change.
- **API**: `POST /api/graph-runs` returns immediately (assert response time
  is not correlated with graph duration); `GET /api/graph-runs/{id}/stream`
  emits events in the correct order and closes on terminal status.

## Explicitly out of scope

- Node-authoring canvas UI, `GraphCanvas.tsx` becoming editable, any
  onboarding-wizard UI work.
- `EditSpec`/operations chaining UI.
- Migrating `graph_persistence.py` off raw `urllib` PostgREST onto Supabase
  Realtime or the Supabase Python SDK — the in-process queue avoids needing
  this for now.
- Removing or modifying `/api/execute` and the existing `subprocess.run`
  based `_run_core` sync path — left in place for existing callers.
