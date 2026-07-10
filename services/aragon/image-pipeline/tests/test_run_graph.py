#!/usr/bin/env python3
"""Tests for core/run_graph.py — graph execution from compile/ manifest."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import compile_bundle  # noqa: E402
import graph_ir  # noqa: E402
import graph_persistence  # noqa: E402
import job_compiler  # noqa: E402
import jobspec  # noqa: E402
import run_graph  # noqa: E402
import run_job  # noqa: E402

JOBS_DIR = os.path.join(os.path.dirname(__file__), "..", "jobs")


class _FakeEngine:
    def cost(self, prompt, output, engine, job_dir="."):
        return {"credits": 2, "model": engine.get("model"), "prompt_len": len(prompt)}

    def generate(self, prompt, reference, output, engine, job_dir="."):
        return [{"result_url": "https://example.com/out.png", "status": "completed"}]

    def execute_plan(self, plan, reference, job_dir=".", dry_run=False):
        prompt = plan.get("prompt", {}).get("positive", "")
        engine = {"model": plan["model"]}
        output = plan.get("output") or {}
        if dry_run or plan.get("method") == "cost_estimate":
            return {"credits": 2, "model": plan["model"], "prompt_len": len(prompt)}
        return [{"result_url": "https://example.com/out.png", "status": "completed"}]


class TestRunGraph(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="run-graph-")
        path = os.path.join(JOBS_DIR, "primikey-usba", "scene-float-a.job.json")
        self.compile_dir = os.path.join(self.tmp, "compile")
        job_compiler.compile_path_to_dir(path, self.compile_dir, dry_run=True)
        self.runtime_bundle = compile_bundle.load_runtime_bundle(self.compile_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    @mock.patch("graph_runtime.adapters.get_engine")
    def test_dry_run_primikey_scene_end_to_end(self, mock_get_engine):
        mock_get_engine.return_value = _FakeEngine()
        result = run_graph.run_graph(
            self.runtime_bundle["graph"],
            runtime_bundle=self.runtime_bundle,
            compile_dir=self.compile_dir,
        )
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["id"], "primikey-usba-scene-float-a")
        self.assertIn("prompt", result)
        self.assertIn("cost", result)
        self.assertEqual(result["cost"]["credits"], 2)
        self.assertIn("compile_hash", result)
        mock_get_engine.assert_called()

    @mock.patch("graph_runtime.adapters.get_engine")
    def test_prompt_parity_with_run_job(self, mock_get_engine):
        mock_get_engine.return_value = _FakeEngine()
        path = os.path.join(JOBS_DIR, "primikey-usba", "scene-float-a.job.json")
        job = jobspec.load(path)
        expected = run_job.build_prompt(job)
        result = run_graph.run_graph(
            self.runtime_bundle["graph"],
            runtime_bundle=self.runtime_bundle,
            compile_dir=self.compile_dir,
        )
        self.assertEqual(result["prompt"], expected)

    def test_graph_validation_failure_raises(self):
        bad_bundle = dict(self.runtime_bundle)
        bad_graph = {
            "schema_version": "graph-v2",
            "id": "bad-graph",
            "nodes": [],
            "edges": [],
        }
        bad_bundle["graph"] = bad_graph
        with self.assertRaises(graph_ir.GraphIRError):
            run_graph.run_graph(bad_graph, runtime_bundle=bad_bundle)

    @mock.patch("graph_persistence.find_idempotent_run")
    @mock.patch("graph_runtime.adapters.get_engine")
    def test_idempotency_key_returns_cached_result(
        self, mock_get_engine, mock_find
    ):
        mock_get_engine.return_value = _FakeEngine()
        cached = {
            "id": "cached-job",
            "dry_run": True,
            "prompt": "cached prompt",
            "_graph_run_id": "00000000-0000-0000-0000-000000000099",
            "_idempotent_hit": True,
        }
        mock_find.return_value = cached
        result = run_graph.run_graph(
            self.runtime_bundle["graph"],
            runtime_bundle=self.runtime_bundle,
            idempotency_key="test-key-1",
            tenant_id="00000000-0000-0000-0000-000000000001",
        )
        self.assertEqual(result["prompt"], "cached prompt")
        self.assertTrue(result.get("_idempotent_hit"))
        mock_find.assert_called_once_with(
            "00000000-0000-0000-0000-000000000001",
            "test-key-1",
        )
        mock_get_engine.assert_not_called()

    def test_topological_order_executes_load_first(self):
        order = graph_ir.topological_sort(self.runtime_bundle["graph"])
        self.assertEqual(order[0], "load")
        self.assertEqual(order[-1], "persist")


if __name__ == "__main__":
    unittest.main()
