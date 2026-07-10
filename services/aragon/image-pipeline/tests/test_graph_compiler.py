#!/usr/bin/env python3
"""Tests for core/graph_compiler.py"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import graph_compiler  # noqa: E402
import graph_ir  # noqa: E402
import job_compiler  # noqa: E402
import jobspec  # noqa: E402

JOBS_DIR = os.path.join(os.path.dirname(__file__), "..", "jobs")


class TestGraphCompiler(unittest.TestCase):
    def test_compiles_primikey_scene_job(self):
        path = os.path.join(JOBS_DIR, "primikey-usba", "scene-float-a.job.json")
        graph = graph_compiler.compile_path(path)
        graph_ir.validate(graph)
        self.assertEqual(graph["schema_version"], "graph-v1")
        self.assertEqual(len(graph["nodes"]), 4)
        order = graph_ir.topological_sort(graph)
        self.assertEqual(order[0], "load")
        self.assertEqual(order[-1], "persist")

    def test_execution_plan_carries_input_images(self):
        path = os.path.join(JOBS_DIR, "primikey-usba", "scene-float-a.job.json")
        artifacts = job_compiler.compile_path(path, dry_run=True)
        imgs = artifacts["execution_plan"]["params"].get("input_images") or []
        self.assertIn("reference/plates/plate-2-hero34.png", imgs)

    def test_preserves_output_imgtype(self):
        path = os.path.join(JOBS_DIR, "primikey-usba", "scene-float-a.job.json")
        graph = graph_compiler.compile_path(path)
        persist = next(n for n in graph["nodes"] if n["id"] == "persist")
        self.assertEqual(persist["params"]["imgtype"], "scene")

    def test_compiles_job_with_reference(self):
        job = {
            "schema_version": "job-v1",
            "id": "sample-shot-with-ref",
            "spec": {"prompt_template": "Product render: {product}."},
            "subject": {"product": "hardware token"},
            "reference": "reference/product-hero.png",
            "output": {"imgtype": "hero", "count": 1},
            "engine": {"name": "higgsfield", "model": "nano_banana_2"},
            "_dir": "/tmp",
        }
        graph = graph_compiler.compile_job(job)
        graph_ir.validate(graph)
        gen = next(n for n in graph["nodes"] if n["id"] == "generate")
        self.assertEqual(
            gen["ports"]["in"]["refs"]["type"],
            "image_path",
        )

    def test_compiles_multi_reference_list(self):
        job = {
            "schema_version": "job-v1",
            "id": "sample-multi-ref-shot",
            "spec": {"prompt_template": "Composite using {product}."},
            "subject": {"product": "hardware token"},
            "reference": ["reference/pose.png", "reference/product.png"],
            "output": {"imgtype": "hero", "count": 1},
            "engine": {"name": "higgsfield", "model": "nano_banana_2"},
            "_dir": "/tmp",
        }
        graph = graph_compiler.compile_job(job)
        graph_ir.validate(graph)
        gen = next(n for n in graph["nodes"] if n["id"] == "generate")
        self.assertEqual(gen["ports"]["in"]["refs"]["type"], "image_path_list")

    def test_preserves_engine_model_and_reference_path(self):
        path = os.path.join(JOBS_DIR, "primikey-usba", "scene-float-a.job.json")
        artifacts = job_compiler.compile_path(path, dry_run=True)
        job_engine = artifacts["execution_request"]["engine"]
        job_reference = artifacts["execution_request"]["reference"]

        graph = graph_compiler.compile_path(path)
        generate = next(n for n in graph["nodes"] if n["id"] == "generate")
        resolve_refs = next(n for n in graph["nodes"] if n["id"] == "resolve_refs")

        self.assertEqual(generate["params"]["model"], job_engine["model"])
        self.assertEqual(generate["params"]["provider"], job_engine["name"])
        self.assertEqual(resolve_refs["params"]["reference"], job_reference)

    def test_preserves_engine_model_for_inline_job_with_reference(self):
        job = {
            "schema_version": "job-v1",
            "id": "sample-shot-with-ref",
            "spec": {"prompt_template": "Product render: {product}."},
            "subject": {"product": "hardware token"},
            "reference": "reference/product-hero.png",
            "output": {"imgtype": "hero", "count": 1},
            "engine": {"name": "higgsfield", "model": "nano_banana_2"},
            "_dir": "/tmp",
        }
        graph = graph_compiler.compile_job(job)
        gen = next(n for n in graph["nodes"] if n["id"] == "generate")
        resolve_refs = next(n for n in graph["nodes"] if n["id"] == "resolve_refs")
        self.assertEqual(gen["params"]["model"], "nano_banana_2")
        self.assertEqual(gen["params"]["provider"], "higgsfield")
        self.assertEqual(resolve_refs["params"]["reference"], "reference/product-hero.png")

    def test_metadata_compiled_from_artifacts(self):
        path = os.path.join(JOBS_DIR, "primikey-usba", "scene-float-a.job.json")
        graph = graph_compiler.compile_path(path)
        self.assertTrue(
            graph["metadata"]["compiled_from"].startswith("compile-artifacts-v1:")
        )

    def test_invalid_job_raises(self):
        with self.assertRaises(jobspec.JobSpecError):
            graph_compiler.compile_job({"schema_version": "job-v2", "id": "bad"})


if __name__ == "__main__":
    unittest.main()
