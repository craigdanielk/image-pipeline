"""Tests for per-node execution_plan override and the result.as_ref node type."""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import graph_runtime  # noqa: E402


class TestPerNodeExecutionPlan(unittest.TestCase):
    def test_engine_generate_uses_node_params_plan_when_present(self):
        node_plan = {
            "provider": "higgsfield",
            "model": "nano_banana_2",
            "method": "cost_estimate",
            "params": {},
            "prompt": {"positive": "a red hairband on a plain background", "negative": None},
            "output": {},
        }
        params = {"execution_plan": node_plan}
        ctx = {"dry_run": True, "artifacts": {"execution_plan": {"prompt": {"positive": "WRONG SHARED PROMPT"}}}}
        fake_adapter = mock.MagicMock()
        fake_adapter.execute_plan.return_value = {"credits": 3}
        with mock.patch.object(graph_runtime.adapters, "get_engine", return_value=fake_adapter):
            out = graph_runtime._exec_engine_generate({"refs": None}, params, ctx)
        self.assertEqual(out, {"results": []})
        called_plan = fake_adapter.execute_plan.call_args[0][0]
        self.assertEqual(called_plan["prompt"]["positive"], "a red hairband on a plain background")

    def test_engine_generate_falls_back_to_shared_ctx_artifacts_when_no_node_params(self):
        artifacts = {
            "execution_plan": {
                "provider": "higgsfield",
                "model": "nano_banana_2",
                "method": "cost_estimate",
                "params": {},
                "prompt": {"positive": "shared prompt", "negative": None},
                "output": {},
            }
        }
        ctx = {"dry_run": True}
        fake_adapter = mock.MagicMock()
        fake_adapter.execute_plan.return_value = {"credits": 1}
        with mock.patch.object(graph_runtime.adapters, "get_engine", return_value=fake_adapter):
            out = graph_runtime._exec_engine_generate({"artifacts": artifacts, "refs": None}, {}, ctx)
        self.assertEqual(out, {"results": []})
        called_plan = fake_adapter.execute_plan.call_args[0][0]
        self.assertEqual(called_plan["prompt"]["positive"], "shared prompt")

    def test_result_as_ref_extracts_result_url_from_prior_node_results(self):
        inputs = {"results": [{"id": "job-1", "status": "completed", "result_url": "https://cdn.example/out.png"}]}
        out = graph_runtime._exec_result_as_ref(inputs, {}, {"dry_run": False})
        self.assertEqual(out, {"refs": "https://cdn.example/out.png"})

    def test_result_as_ref_returns_none_in_dry_run_with_no_results(self):
        out = graph_runtime._exec_result_as_ref({"results": []}, {}, {"dry_run": True})
        self.assertEqual(out, {"refs": None})

    def test_result_as_ref_is_registered_node_type(self):
        self.assertIn("result.as_ref", graph_runtime.NODE_TYPES)
        node = {"type": "result.as_ref", "params": {}}
        out = graph_runtime.execute_node(
            node,
            {"results": [{"result_url": "https://cdn.example/x.png"}]},
            {"dry_run": False},
        )
        self.assertEqual(out, {"refs": "https://cdn.example/x.png"})


if __name__ == "__main__":
    unittest.main()
