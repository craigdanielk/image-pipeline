#!/usr/bin/env python3
"""graph_ir — Phase 1 graph intermediate representation (typed edges).

The IR models a pipeline as nodes (each a registered node type) plus edges
connecting a source node's output port to a destination node's input port.
Edge validity is delegated to :mod:`node_registry`, which is the single source
of truth for node types and port typing — this module never hardcodes the port
compatibility table.

Out of scope (Phase 2): execution scheduler, run_graph runtime, persistence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import node_registry


@dataclass(frozen=True)
class Edge:
    """Directed, port-to-port connection between two graph nodes."""

    src_node: str
    src_port: str
    dst_node: str
    dst_port: str


@dataclass
class GraphIR:
    """A validated DAG of registered node types."""

    nodes: dict[str, str] = field(default_factory=dict)  # node_id -> node_type
    edges: list[Edge] = field(default_factory=list)

    def add_node(self, node_id: str, node_type: str) -> None:
        node_registry.validate_node_type(node_type)  # registry lookup
        self.nodes[node_id] = node_type

    def add_edge(
        self, src_node: str, src_port: str, dst_node: str, dst_port: str
    ) -> Edge:
        """Add an edge, enforcing registry-driven port compatibility."""
        if src_node not in self.nodes:
            raise node_registry.NodeRegistryError(f"unknown src node {src_node!r}")
        if dst_node not in self.nodes:
            raise node_registry.NodeRegistryError(f"unknown dst node {dst_node!r}")
        # AC: registry lookup used by graph_ir for port compatibility checks.
        if not node_registry.ports_compatible(
            self.nodes[src_node], src_port, self.nodes[dst_node], dst_port
        ):
            raise node_registry.NodeRegistryError(
                f"incompatible edge {src_node}.{src_port} -> {dst_node}.{dst_port}"
            )
        edge = Edge(src_node, src_port, dst_node, dst_port)
        self.edges.append(edge)
        return edge

    def validate(self) -> bool:
        """Re-check every node type and edge against the registry."""
        for node_type in self.nodes.values():
            node_registry.validate_node_type(node_type)
        for e in self.edges:
            if not node_registry.ports_compatible(
                self.nodes[e.src_node], e.src_port, self.nodes[e.dst_node], e.dst_port
            ):
                raise node_registry.NodeRegistryError(
                    f"incompatible edge {e.src_node}.{e.src_port} -> "
                    f"{e.dst_node}.{e.dst_port}"
                )
        return True


def linear_phase1_graph() -> GraphIR:
    """Build the canonical six-stage Phase 1 pipeline as a GraphIR."""
    g = GraphIR()
    for i, ntype in enumerate(node_registry.PHASE1_NODE_TYPES):
        g.add_node(f"n{i}", ntype)
    # Wire the compatible ports along the linear pipeline.
    g.add_edge("n0", "cad", "n1", "cad")            # cad.load -> prompt.build
    g.add_edge("n1", "prompt", "n3", "prompt")      # prompt.build -> engine.cost
    g.add_edge("n2", "refs", "n3", "refs")          # reference.resolve -> engine.cost
    g.add_edge("n1", "prompt", "n4", "prompt")      # prompt.build -> engine.generate
    g.add_edge("n3", "cost", "n4", "cost")          # engine.cost -> engine.generate
    g.add_edge("n4", "results", "n5", "results")    # engine.generate -> output.persist
    return g


__all__ = ["Edge", "GraphIR", "linear_phase1_graph"]
