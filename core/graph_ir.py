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
