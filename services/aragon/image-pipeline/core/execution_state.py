#!/usr/bin/env python3
"""Execution state machine — per-node status, checkpoints, and lineage.

The runtime (``run_graph`` / ``_schedule``) drives an in-memory
``graph_ir.GraphFrontier`` for scheduling. ``execution_state`` is the
*persistence-facing* mirror of that frontier: it records the canonical
per-node status transitions, checkpoints them to disk so an interrupted
graph can be resumed, and mirrors them into the Supabase tracking tables
(``graph_runs`` / ``graph_node_runs``) with ``parent_job_id`` lineage.

Design constraints (match image-pipeline core):
  * dep-free, bare Python 3.9+ (no jsonschema, no pydantic)
  * no LLM calls, no provider I/O — this is bookkeeping only
  * deterministic: identical inputs → identical checkpoint content

State model (canonical persisted vocabulary)::

    PENDING ──▶ RUNNING ──▶ COMPLETED
        │           │
        │           └────▶ FAILED
        └────────────────▶ SKIPPED   (transitive downstream of a failure)

``RUNNING`` is the persisted spelling of the frontier's internal
``dispatched`` state — the DB/checkpoint view speaks in run terms.

Deterministic replay
--------------------
``ExecutionState.load_checkpoint`` rehydrates a prior run's node statuses.
``resume_frontier`` returns the set of already-COMPLETED node ids so the
scheduler can replay from the first incomplete node — re-executing a failed
node and its descendants only, while completed upstream nodes are skipped.
On a cache-miss path the re-executed nodes recompute an identical cost
estimate because compilation is content-addressed upstream of the graph.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

try:  # optional Supabase mirror — off unless tracking env is configured
    import graph_persistence  # type: ignore
except Exception:  # pragma: no cover - persistence is best-effort
    graph_persistence = None  # type: ignore


# ---------------------------------------------------------------------------
# Canonical persisted status vocabulary + legal transitions.
# ---------------------------------------------------------------------------
PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
SKIPPED = "skipped"

TERMINAL = frozenset({COMPLETED, FAILED, SKIPPED})

# Maps the scheduler frontier's internal spelling to the persisted vocabulary.
_FRONTIER_ALIAS = {"dispatched": RUNNING}

_LEGAL_TRANSITIONS: dict[str, frozenset] = {
    PENDING: frozenset({RUNNING, SKIPPED}),
    RUNNING: frozenset({COMPLETED, FAILED}),
    COMPLETED: frozenset(),
    FAILED: frozenset(),
    SKIPPED: frozenset(),
}

CHECKPOINT_SCHEMA = "execution-state-v1"


class ExecutionStateError(ValueError):
    """Raised on an illegal state transition or a corrupt checkpoint."""


def normalize_status(status: str) -> str:
    """Map a frontier status onto the canonical persisted vocabulary."""
    return _FRONTIER_ALIAS.get(status, status)


@dataclass
class NodeState:
    """One node's execution record."""

    node_id: str
    node_type: str
    status: str = PENDING
    input_hash: str | None = None
    output_snapshot: dict[str, Any] | None = None
    error: str | None = None
    external_job_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "status": self.status,
            "input_hash": self.input_hash,
            "output_snapshot": self.output_snapshot,
            "error": self.error,
            "external_job_ref": self.external_job_ref,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "NodeState":
        return cls(
            node_id=d["node_id"],
            node_type=d.get("node_type", ""),
            status=d.get("status", PENDING),
            input_hash=d.get("input_hash"),
            output_snapshot=d.get("output_snapshot"),
            error=d.get("error"),
            external_job_ref=d.get("external_job_ref"),
        )


class ExecutionState:
    """Authoritative per-node execution record for a single graph run.

    Holds the canonical status of every node, enforces legal transitions,
    checkpoints to disk, and (best-effort) mirrors into Supabase with
    ``parent_job_id`` lineage. Independent of the async scheduler so it can
    be unit-tested and reloaded without an event loop.
    """

    def __init__(
        self,
        graph: dict[str, Any],
        *,
        graph_run_id: str | None = None,
        parent_job_id: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        self.graph_id: str = graph.get("id", "unknown")
        self.graph_run_id = graph_run_id
        self.parent_job_id = parent_job_id
        self.tenant_id = tenant_id
        self.nodes: dict[str, NodeState] = {}
        for node in graph.get("nodes") or []:
            nid = node["id"]
            self.nodes[nid] = NodeState(node_id=nid, node_type=node.get("type", ""))

    # -- transitions --------------------------------------------------------
    def transition(
        self,
        node_id: str,
        status: str,
        *,
        input_hash: str | None = None,
        output_snapshot: dict[str, Any] | None = None,
        error: str | None = None,
        external_job_ref: str | None = None,
        mirror: bool = False,
    ) -> None:
        """Move a node to ``status``, enforcing the legal transition graph."""
        status = normalize_status(status)
        node = self.nodes.get(node_id)
        if node is None:
            raise ExecutionStateError(f"unknown node_id {node_id!r}")
        if status not in _LEGAL_TRANSITIONS:
            raise ExecutionStateError(f"unknown status {status!r}")
        if status != node.status and status not in _LEGAL_TRANSITIONS[node.status]:
            raise ExecutionStateError(
                f"illegal transition for {node_id!r}: {node.status!r} -> {status!r}"
            )
        node.status = status
        if input_hash is not None:
            node.input_hash = input_hash
        if output_snapshot is not None:
            node.output_snapshot = output_snapshot
        if error is not None:
            node.error = error
        if external_job_ref is not None:
            node.external_job_ref = external_job_ref
        if mirror:
            self.mirror_node(node_id)

    # -- queries ------------------------------------------------------------
    def statuses(self) -> dict[str, str]:
        return {nid: n.status for nid, n in self.nodes.items()}

    def completed_nodes(self) -> set[str]:
        return {nid for nid, n in self.nodes.items() if n.status == COMPLETED}

    def is_complete(self) -> bool:
        return all(n.status in TERMINAL for n in self.nodes.values())

    def run_status(self) -> str:
        """Roll node statuses up to an overall run verdict."""
        values = [n.status for n in self.nodes.values()]
        if not values:
            return FAILED
        completed = sum(1 for s in values if s == COMPLETED)
        if completed == len(values):
            return COMPLETED
        if completed == 0:
            return FAILED
        return "partial"

    # -- checkpoint persistence (disk) --------------------------------------
    def to_checkpoint(self) -> dict[str, Any]:
        return {
            "schema_version": CHECKPOINT_SCHEMA,
            "graph_id": self.graph_id,
            "graph_run_id": self.graph_run_id,
            "parent_job_id": self.parent_job_id,
            "tenant_id": self.tenant_id,
            "nodes": [n.to_dict() for n in self.nodes.values()],
        }

    def save_checkpoint(self, path: str) -> str:
        """Atomically write the checkpoint JSON to ``path``."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_checkpoint(), fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return path

    @classmethod
    def from_checkpoint(cls, data: dict[str, Any]) -> "ExecutionState":
        if data.get("schema_version") != CHECKPOINT_SCHEMA:
            raise ExecutionStateError(
                f"bad checkpoint schema {data.get('schema_version')!r}"
            )
        # rebuild a minimal graph-shaped stub so __init__ can seed node ids
        stub = {
            "id": data.get("graph_id", "unknown"),
            "nodes": [
                {"id": n["node_id"], "type": n.get("node_type", "")}
                for n in data.get("nodes") or []
            ],
        }
        state = cls(
            stub,
            graph_run_id=data.get("graph_run_id"),
            parent_job_id=data.get("parent_job_id"),
            tenant_id=data.get("tenant_id"),
        )
        for n in data.get("nodes") or []:
            state.nodes[n["node_id"]] = NodeState.from_dict(n)
        return state

    @classmethod
    def load_checkpoint(cls, path: str) -> "ExecutionState":
        with open(path, encoding="utf-8") as fh:
            return cls.from_checkpoint(json.load(fh))

    # -- Supabase mirror (best-effort, off unless tracking configured) ------
    def mirror_node(self, node_id: str) -> None:
        """Mirror one node's current state into ``graph_node_runs``."""
        if graph_persistence is None or not self.graph_run_id:
            return
        node = self.nodes[node_id]
        try:
            graph_persistence.record_node_run(
                self.graph_run_id,
                node_id=node.node_id,
                node_type=node.node_type,
                status=node.status,
                input_hash=node.input_hash,
                output_snapshot=node.output_snapshot,
                error=node.error,
            )
        except Exception:
            pass

    def mirror_all(self) -> None:
        for nid in self.nodes:
            self.mirror_node(nid)


def resume_frontier(
    graph: dict[str, Any],
    state: "ExecutionState",
):
    """Rebuild a scheduler frontier that replays already-COMPLETED nodes.

    Deterministic replay: every node the checkpoint marks COMPLETED is
    dispatched-and-completed on the returned frontier so scheduling resumes
    from the first incomplete node. A previously FAILED node returns to the
    ready set and re-executes (along with its descendants).
    """
    import graph_ir

    frontier = graph_ir.GraphFrontier(graph)
    completed = state.completed_nodes()
    # Replay in dependency order: a node can only complete once its
    # upstreams have. Iterate ready() until no completed node remains ready.
    progressed = True
    while progressed:
        progressed = False
        for nid in frontier.ready():
            if nid in completed:
                frontier.dispatch(nid)
                frontier.complete(nid)
                progressed = True
    return frontier


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 2:
        st = ExecutionState.load_checkpoint(sys.argv[1])
        print(json.dumps({"graph_id": st.graph_id, "statuses": st.statuses()}, indent=2))
    else:
        print("Usage: python execution_state.py <checkpoint.json>", file=sys.stderr)
        raise SystemExit(1)
