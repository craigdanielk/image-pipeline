#!/usr/bin/env python3
"""Golden replay — compile dir → dry-run execute (U3)."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import compile_bundle  # noqa: E402
import job_compiler  # noqa: E402
import run_graph  # noqa: E402
import run_job  # noqa: E402

JOBS_DIR = os.path.join(os.path.dirname(__file__), "..", "jobs")


class _FakeEngine:
    def execute_plan(self, plan, reference, job_dir=".", dry_run=False):
        prompt = plan.get("prompt", {}).get("positive", "")
        if dry_run or plan.get("method") == "cost_estimate":
            return {"credits": 2, "model": plan["model"], "prompt_len": len(prompt)}
        return [{"result_url": "https://example.com/out.png", "status": "completed"}]


class TestGoldenReplay(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="golden-replay-")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    @mock.patch("graph_runtime.adapters.get_engine")
    def test_compile_dir_dry_run_end_to_end(self, mock_get_engine) -> None:
        mock_get_engine.return_value = _FakeEngine()
        path = os.path.join(JOBS_DIR, "primikey-usba", "scene-float-a.job.json")
        compile_dir = os.path.join(self.tmp, "compile")
        job_compiler.compile_path_to_dir(path, compile_dir, dry_run=True)

        result = run_graph.run_compile_dir(compile_dir)
        self.assertTrue(result["dry_run"])
        self.assertIn("prompt", result)
        self.assertIn("compile_hash", result)
        self.assertEqual(result["cost"]["credits"], 2)

    @mock.patch("graph_runtime.adapters.get_engine")
    def test_prompt_parity_compile_then_execute(self, mock_get_engine) -> None:
        mock_get_engine.return_value = _FakeEngine()
        path = os.path.join(JOBS_DIR, "primikey-usba", "scene-float-a.job.json")
        import jobspec

        job = jobspec.load(path)
        expected = run_job.build_prompt(job)

        compile_dir = os.path.join(self.tmp, "compile2")
        job_compiler.compile_path_to_dir(path, compile_dir, dry_run=True)
        result = run_graph.run_compile_dir(compile_dir)
        self.assertEqual(result["prompt"], expected)

    def test_recompile_same_manifest_hash(self) -> None:
        path = os.path.join(JOBS_DIR, "primikey-usba", "scene-float-a.job.json")
        d1 = os.path.join(self.tmp, "c1")
        d2 = os.path.join(self.tmp, "c2")
        m1 = job_compiler.compile_path_to_dir(path, d1, dry_run=True)
        m2 = job_compiler.compile_path_to_dir(path, d2, dry_run=True)
        self.assertEqual(m1["manifest_hash"], m2["manifest_hash"])


if __name__ == "__main__":
    unittest.main()
