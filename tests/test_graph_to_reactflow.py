#!/usr/bin/env python3
"""Tests for tools/graph_to_reactflow.py"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import graph_to_reactflow  # noqa: E402

EXAMPLE = os.path.join(
    os.path.dirname(__file__), "..", "schemas", "examples", "graph-v1-linear-pipeline.json"
)


class TestGraphToReactflow(unittest.TestCase):
    def test_converts_example_graph(self) -> None:
        graph = json.loads(open(EXAMPLE, encoding="utf-8").read())
        rf = graph_to_reactflow.graph_v1_to_reactflow(graph)
        self.assertEqual(len(rf["nodes"]), 4)
        self.assertGreaterEqual(len(rf["edges"]), 4)
        ids = {n["id"] for n in rf["nodes"]}
        self.assertIn("load", ids)
        self.assertIn("persist", ids)

    def test_node_status_overlay(self) -> None:
        graph = json.loads(open(EXAMPLE, encoding="utf-8").read())
        rf = graph_to_reactflow.graph_v1_to_reactflow(
            graph, node_status={"load": "completed", "generate": "running"}
        )
        by_id = {n["id"]: n for n in rf["nodes"]}
        self.assertEqual(by_id["load"]["data"]["status"], "completed")
        self.assertEqual(by_id["generate"]["data"]["status"], "running")


if __name__ == "__main__":
    unittest.main()
