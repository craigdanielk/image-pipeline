"""Tests for multi-op chain emission in graph_compiler.compile_execution_graph."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import graph_compiler  # noqa: E402
import graph_ir  # noqa: E402


def _base_artifacts(**overrides):
    artifacts = {
        "schema_version": "compile-artifacts-v1",
        "job_id": "test-job",
        "job_dir": ".",
        "execution_request": {
            "id": "test-job-exec",
            "reference": None,
            "engine": {"name": "higgsfield", "model": "nano_banana_2"},
            "output": {"imgtype": "hero", "dir": "out"},
        },
        "compile_hash": "abc123",
    }
    artifacts.update(overrides)
    return artifacts


class TestSingleNodeBackwardCompat(unittest.TestCase):
    def test_no_op_execution_plans_key_produces_unchanged_4_node_graph(self):
        artifacts = _base_artifacts()
        graph = graph_compiler.compile_execution_graph(artifacts)
        node_ids = [n["id"] for n in graph["nodes"]]
        self.assertEqual(node_ids, ["load", "resolve_refs", "generate", "persist"])
        graph_ir.validate(graph)


class TestMultiOpChainEmission(unittest.TestCase):
    def test_two_ops_produce_two_generate_nodes_chained_by_result_as_ref(self):
        op_plans = [
            {
                "execution_plan": {
                    "provider": "higgsfield", "model": "nano_banana_2", "method": "cost_estimate",
                    "params": {}, "prompt": {"positive": "replace logo", "negative": None}, "output": {},
                },
                "job_dir": ".",
                "op": "replace_logo",
            },
            {
                "execution_plan": {
                    "provider": "higgsfield", "model": "nano_banana_2", "method": "cost_estimate",
                    "params": {}, "prompt": {"positive": "swap subject", "negative": None}, "output": {},
                },
                "job_dir": ".",
                "op": "subject_swap",
            },
        ]
        artifacts = _base_artifacts(op_execution_plans=op_plans)
        graph = graph_compiler.compile_execution_graph(artifacts)
        graph_ir.validate(graph)

        node_ids = [n["id"] for n in graph["nodes"]]
        self.assertEqual(
            node_ids,
            ["load", "resolve_refs", "generate-0-replace_logo", "ref-0-1", "generate-1-subject_swap", "persist"],
        )

        gen0 = next(n for n in graph["nodes"] if n["id"] == "generate-0-replace_logo")
        gen1 = next(n for n in graph["nodes"] if n["id"] == "generate-1-subject_swap")
        self.assertEqual(gen0["params"]["execution_plan"]["prompt"]["positive"], "replace logo")
        self.assertEqual(gen1["params"]["execution_plan"]["prompt"]["positive"], "swap subject")

        edge_pairs = {(e["from"]["node"], e["to"]["node"]) for e in graph["edges"]}
        self.assertIn(("resolve_refs", "generate-0-replace_logo"), edge_pairs)
        self.assertIn(("generate-0-replace_logo", "ref-0-1"), edge_pairs)
        self.assertIn(("ref-0-1", "generate-1-subject_swap"), edge_pairs)
        self.assertIn(("generate-1-subject_swap", "persist"), edge_pairs)
        # persist must NOT also wire directly from generate-0 — only the last node feeds persist
        self.assertNotIn(("generate-0-replace_logo", "persist"), edge_pairs)

    def test_single_op_execution_plans_list_of_one_produces_one_generate_node_no_chain_link(self):
        op_plans = [
            {
                "execution_plan": {
                    "provider": "higgsfield", "model": "nano_banana_2", "method": "cost_estimate",
                    "params": {}, "prompt": {"positive": "remove background", "negative": None}, "output": {},
                },
                "job_dir": ".",
                "op": "remove_background",
            },
        ]
        artifacts = _base_artifacts(op_execution_plans=op_plans)
        graph = graph_compiler.compile_execution_graph(artifacts)
        graph_ir.validate(graph)
        node_ids = [n["id"] for n in graph["nodes"]]
        self.assertEqual(node_ids, ["load", "resolve_refs", "generate-0-remove_background", "persist"])
        self.assertNotIn("result.as_ref", [n["type"] for n in graph["nodes"]])


if __name__ == "__main__":
    unittest.main()
