#!/usr/bin/env python3
"""Tests for core/graph_ir.py"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import graph_ir  # noqa: E402


def _minimal_graph(edges=None, extra_node=None):
    nodes = [
        {
            "id": "a",
            "type": "intake.load",
            "ports": {"out": {"job": {"type": "job_spec"}}},
        },
        {
            "id": "b",
            "type": "jobspec.validate",
            "ports": {
                "in": {"job": {"type": "job_spec"}},
                "out": {"job": {"type": "job_spec"}},
            },
        },
        {
            "id": "c",
            "type": "prompt.build",
            "ports": {
                "in": {"job": {"type": "job_spec"}},
                "out": {"prompt": {"type": "prompt"}},
            },
        },
    ]
    if extra_node:
        nodes.append(extra_node)
    return {
        "schema_version": "graph-v1",
        "id": "test-graph",
        "nodes": nodes,
        "edges": edges
        if edges is not None
        else [
            {"from": {"node": "a", "port": "job"}, "to": {"node": "b", "port": "job"}},
            {"from": {"node": "b", "port": "job"}, "to": {"node": "c", "port": "job"}},
        ],
    }


class TestGraphIR(unittest.TestCase):
    def test_example_linear_pipeline_valid(self):
        path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "schemas",
            "examples",
            "graph-v1-linear-pipeline.json",
        )
        g = graph_ir.load(path)
        order = graph_ir.topological_sort(g)
        self.assertEqual(order[0], "load")
        self.assertEqual(order[-1], "persist")
        self.assertEqual(len(order), 4)

    def test_topological_sort_valid_dag(self):
        g = _minimal_graph()
        self.assertEqual(graph_ir.topological_sort(g), ["a", "b", "c"])

    def test_cycle_rejected(self):
        job_ports = {
            "in": {"job": {"type": "job_spec"}},
            "out": {"job": {"type": "job_spec"}},
        }
        g = {
            "schema_version": "graph-v1",
            "id": "cycle-graph",
            "nodes": [
                {"id": "a", "type": "jobspec.validate", "ports": job_ports},
                {"id": "b", "type": "jobspec.validate", "ports": job_ports},
                {"id": "c", "type": "jobspec.validate", "ports": job_ports},
            ],
            "edges": [
                {"from": {"node": "a", "port": "job"}, "to": {"node": "b", "port": "job"}},
                {"from": {"node": "b", "port": "job"}, "to": {"node": "c", "port": "job"}},
                {"from": {"node": "c", "port": "job"}, "to": {"node": "a", "port": "job"}},
            ],
        }
        with self.assertRaises(graph_ir.GraphIRError) as ctx:
            graph_ir.validate(g)
        self.assertIn("cycle", str(ctx.exception).lower())

    def test_missing_input_port_rejected(self):
        g = _minimal_graph(
            edges=[
                {"from": {"node": "a", "port": "job"}, "to": {"node": "b", "port": "missing"}},
            ]
        )
        with self.assertRaises(graph_ir.GraphIRError) as ctx:
            graph_ir.validate(g)
        self.assertIn("missing input port", str(ctx.exception))

    def test_missing_output_port_rejected(self):
        g = _minimal_graph(
            edges=[
                {"from": {"node": "a", "port": "missing"}, "to": {"node": "b", "port": "job"}},
            ]
        )
        with self.assertRaises(graph_ir.GraphIRError) as ctx:
            graph_ir.validate(g)
        self.assertIn("missing output port", str(ctx.exception))

    def test_type_mismatch_rejected(self):
        g = {
            "schema_version": "graph-v1",
            "id": "mismatch-graph",
            "nodes": [
                {
                    "id": "a",
                    "type": "prompt.build",
                    "ports": {
                        "in": {"job": {"type": "job_spec"}},
                        "out": {"prompt": {"type": "prompt"}},
                    },
                },
                {
                    "id": "b",
                    "type": "jobspec.validate",
                    "ports": {
                        "in": {"job": {"type": "job_spec"}},
                        "out": {"job": {"type": "job_spec"}},
                    },
                },
            ],
            "edges": [
                {"from": {"node": "a", "port": "prompt"}, "to": {"node": "b", "port": "job"}},
            ],
        }
        with self.assertRaises(graph_ir.GraphIRError) as ctx:
            graph_ir.validate(g)
        self.assertIn("type mismatch", str(ctx.exception))

    def test_load_from_file(self):
        g = _minimal_graph()
        with tempfile.NamedTemporaryFile("w", suffix=".graph.json", delete=False) as f:
            json.dump(g, f)
            path = f.name
        try:
            loaded = graph_ir.load(path)
            self.assertEqual(loaded["id"], "test-graph")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
