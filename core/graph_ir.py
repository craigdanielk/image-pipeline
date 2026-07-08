#!/usr/bin/env python3
"""Graph IR — load, validate, and plan graph-v1 execution DAGs.

Hand-validates the fields the runner depends on (same philosophy as jobspec.py).
schemas/graph-v1.schema.json is the published contract for editors and future API.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict, deque
from typing import Any

import node_registry

SCHEMA_VERSION = "graph-v1"


class GraphIRError(ValueError):
    """Raised when a graph-v1 document is malformed or not a valid execution DAG."""


def load(path: str) -> dict[str, Any]:
    """Read a graph-v1 JSON file, validate, return dict."""
    with open(path) as f:
        graph = json.load(f)
    validate(graph)
    return graph


def load_dict(graph: dict[str, Any]) -> dict[str, Any]:
    """Validate an in-memory graph dict."""
    validate(graph)
    return graph


def validate(graph: dict[str, Any]) -> bool:
    """Structural + DAG + port compatibility validation. Raises GraphIRError."""
    if not isinstance(graph, dict):
        raise GraphIRError("Graph must be a JSON object.")

    ver = graph.get("schema_version")
    if ver != SCHEMA_VERSION:
        raise GraphIRError(f"schema_version must be '{SCHEMA_VERSION}', got {ver!r}.")

    gid = graph.get("id", "")
    if not gid or not _is_slug(gid):
        raise GraphIRError(f"id must be a lowercase slug [a-z0-9-], got {gid!r}.")

    nodes = graph.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise GraphIRError("nodes must be a non-empty array.")

    node_index: dict[str, dict] = {}
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise GraphIRError(f"nodes[{i}] must be an object.")
        nid = node.get("id")
        if not nid or not _is_node_id(nid):
            raise GraphIRError(f"nodes[{i}].id invalid: {nid!r}")
        if nid in node_index:
            raise GraphIRError(f"duplicate node id: {nid!r}")
        ntype = node.get("type")
        if not ntype or not isinstance(ntype, str):
            raise GraphIRError(f"nodes[{nid}].type is required.")
        try:
            node_registry.validate_node_type(ntype)
            node_registry.validate_node_ports(ntype, node.get("ports"))
        except node_registry.NodeRegistryError as exc:
            raise GraphIRError(f"nodes[{nid}]: {exc}") from exc
        ports = node.get("ports") or {}
        if ports and not isinstance(ports, dict):
            raise GraphIRError(f"nodes[{nid}].ports must be an object.")
        for direction in ("in", "out"):
            side = ports.get(direction)
            if side is None:
                continue
            if not isinstance(side, dict):
                raise GraphIRError(f"nodes[{nid}].ports.{direction} must be an object.")
            for pname, pspec in side.items():
                if not isinstance(pspec, dict) or not pspec.get("type"):
                    raise GraphIRError(
                        f"nodes[{nid}].ports.{direction}.{pname} requires a type string."
                    )
        node_index[nid] = node

    edges = graph.get("edges")
    if not isinstance(edges, list):
        raise GraphIRError("edges must be an array.")

    adj: dict[str, list[str]] = {nid: [] for nid in node_index}
    indegree: dict[str, int] = {nid: 0 for nid in node_index}

    for i, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise GraphIRError(f"edges[{i}] must be an object.")
        src = edge.get("from")
        dst = edge.get("to")
        if not _valid_endpoint(src) or not _valid_endpoint(dst):
            raise GraphIRError(f"edges[{i}] requires from/to {{node, port}} endpoints.")

        src_node, src_port = src["node"], src["port"]
        dst_node, dst_port = dst["node"], dst["port"]

        if src_node not in node_index:
            raise GraphIRError(f"edges[{i}] from unknown node: {src_node!r}")
        if dst_node not in node_index:
            raise GraphIRError(f"edges[{i}] to unknown node: {dst_node!r}")

        src_ports = (node_index[src_node].get("ports") or {}).get("out") or {}
        dst_ports = (node_index[dst_node].get("ports") or {}).get("in") or {}

        if src_port not in src_ports:
            raise GraphIRError(
                f"edges[{i}] missing output port {src_node}.{src_port}"
            )
        if dst_port not in dst_ports:
            raise GraphIRError(
                f"edges[{i}] missing input port {dst_node}.{dst_port}"
            )

        src_type = src_ports[src_port]["type"]
        dst_type = dst_ports[dst_port]["type"]
        if not _types_compatible(src_type, dst_type):
            raise GraphIRError(
                f"edges[{i}] type mismatch: {src_node}.{src_port} ({src_type}) "
                f"→ {dst_node}.{dst_port} ({dst_type})"
            )

        if src_node != dst_node:
            adj[src_node].append(dst_node)
            indegree[dst_node] += 1

    order = _topological_sort(node_index.keys(), adj, indegree)
    if order is None:
        raise GraphIRError("graph contains a cycle — not a DAG.")
    return True


def topological_sort(graph: dict[str, Any]) -> list[str]:
    """Return node ids in a valid execution order. Raises if not a DAG."""
    validate(graph)
    node_ids = [n["id"] for n in graph["nodes"]]
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    indegree: dict[str, int] = {nid: 0 for nid in node_ids}
    index = {n["id"]: n for n in graph["nodes"]}

    for edge in graph.get("edges") or []:
        src_node = edge["from"]["node"]
        dst_node = edge["to"]["node"]
        if src_node != dst_node:
            adj[src_node].append(dst_node)
            indegree[dst_node] += 1

    order = _topological_sort(node_ids, adj, indegree)
    if order is None:
        raise GraphIRError("graph contains a cycle — not a DAG.")
    return order


def _topological_sort(
    node_ids: list[str] | Any,
    adj: dict[str, list[str]],
    indegree: dict[str, int],
) -> list[str] | None:
    """Kahn's algorithm. Returns None if cycle detected."""
    q = deque([n for n in node_ids if indegree[n] == 0])
    order: list[str] = []
    while q:
        n = q.popleft()
        order.append(n)
        for m in adj[n]:
            indegree[m] -= 1
            if indegree[m] == 0:
                q.append(m)
    if len(order) != len(list(node_ids)):
        return None
    return order


class GraphFrontier:
    """Live, mutable ready-set over a validated DAG.

    Replaces drain-to-flat-list scheduling (topological_sort) with an
    incremental frontier: callers dispatch every ready node concurrently,
    then feed back complete()/fail() as results arrive.

    Node states: pending -> dispatched -> completed | failed
                 pending -> skipped   (transitive downstream of a failure)

    The graph is assumed already validated (run through validate()); the
    frontier only consumes node ids and edge endpoints.
    """

    PENDING = "pending"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

    def __init__(self, graph: dict[str, Any]) -> None:
        node_ids = [n["id"] for n in graph.get("nodes") or []]
        self._order = list(node_ids)
        self._adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
        self._indegree: dict[str, int] = {nid: 0 for nid in node_ids}
        for edge in graph.get("edges") or []:
            src = (edge.get("from") or {}).get("node")
            dst = (edge.get("to") or {}).get("node")
            if (
                src
                and dst
                and src != dst
                and src in self._adj
                and dst in self._adj
            ):
                self._adj[src].append(dst)
                self._indegree[dst] += 1
        self._state: dict[str, str] = {nid: self.PENDING for nid in node_ids}

    def ready(self) -> list[str]:
        """All zero-indegree nodes not yet dispatched, in graph node order."""
        return [
            nid
            for nid in self._order
            if self._state[nid] == self.PENDING and self._indegree[nid] == 0
        ]

    def dispatch(self, node_id: str) -> None:
        """Mark a ready node as in-flight so ready() stops returning it."""
        if self._state.get(node_id) != self.PENDING:
            raise GraphIRError(
                f"cannot dispatch {node_id!r}: state={self._state.get(node_id)!r}"
            )
        if self._indegree[node_id] != 0:
            raise GraphIRError(f"cannot dispatch {node_id!r}: upstream incomplete")
        self._state[node_id] = self.DISPATCHED

    def complete(self, node_id: str) -> None:
        """Successful completion — unlock dependents."""
        if self._state.get(node_id) != self.DISPATCHED:
            raise GraphIRError(
                f"cannot complete {node_id!r}: state={self._state.get(node_id)!r}"
            )
        self._state[node_id] = self.COMPLETED
        for m in self._adj[node_id]:
            self._indegree[m] -= 1

    def fail(self, node_id: str) -> list[str]:
        """Mark node failed; transitively skip every not-yet-completed
        node reachable from it. Returns newly skipped node ids."""
        if self._state.get(node_id) != self.DISPATCHED:
            raise GraphIRError(
                f"cannot fail {node_id!r}: state={self._state.get(node_id)!r}"
            )
        self._state[node_id] = self.FAILED
        skipped: list[str] = []
        stack = list(self._adj[node_id])
        seen: set[str] = set()
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            if self._state[n] == self.PENDING:
                self._state[n] = self.SKIPPED
                skipped.append(n)
            stack.extend(self._adj[n])
        return skipped

    def done(self) -> bool:
        """True when no node is pending or in flight."""
        return all(
            s not in (self.PENDING, self.DISPATCHED) for s in self._state.values()
        )

    def states(self) -> dict[str, str]:
        return dict(self._state)


def slice_to_target(graph: dict[str, Any], target_node_id: str) -> dict[str, Any]:
    """Restrict graph to target_node_id plus its ancestors (reverse-BFS).

    Nodes outside the ancestor set are dropped from the returned copy —
    not skipped, simply not part of the run. Raises GraphIRError if the
    target is unknown.
    """
    node_ids = {n["id"] for n in graph.get("nodes") or []}
    if target_node_id not in node_ids:
        raise GraphIRError(f"target_node_id not in graph: {target_node_id!r}")

    rev: dict[str, list[str]] = defaultdict(list)
    for edge in graph.get("edges") or []:
        src = (edge.get("from") or {}).get("node")
        dst = (edge.get("to") or {}).get("node")
        if src and dst and src != dst:
            rev[dst].append(src)

    keep = {target_node_id}
    stack = [target_node_id]
    while stack:
        n = stack.pop()
        for p in rev.get(n, []):
            if p not in keep:
                keep.add(p)
                stack.append(p)

    sliced = dict(graph)
    sliced["nodes"] = [n for n in graph["nodes"] if n["id"] in keep]
    sliced["edges"] = [
        e
        for e in graph.get("edges") or []
        if (e.get("from") or {}).get("node") in keep
        and (e.get("to") or {}).get("node") in keep
    ]
    return sliced


def _types_compatible(src: str, dst: str) -> bool:
    return node_registry.types_compatible(src, dst)


def _is_slug(s: str) -> bool:
    return bool(s) and all(c.islower() or c.isdigit() or c == "-" for c in s)


def _is_node_id(s: str) -> bool:
    return bool(s) and all(c.islower() or c.isdigit() or c in "-_" for c in s)


def _valid_endpoint(ep: Any) -> bool:
    return (
        isinstance(ep, dict)
        and isinstance(ep.get("node"), str)
        and isinstance(ep.get("port"), str)
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python graph_ir.py <graph.json>", file=sys.stderr)
        raise SystemExit(1)
    g = load(sys.argv[1])
    order = topological_sort(g)
    print(json.dumps({"id": g["id"], "order": order}, indent=2))
