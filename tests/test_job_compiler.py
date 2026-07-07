#!/usr/bin/env python3
"""Tests for core/job_compiler.py — full compile chain."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import job_compiler  # noqa: E402
import jobspec  # noqa: E402
import spec_validator  # noqa: E402

JOBS_DIR = os.path.join(os.path.dirname(__file__), "..", "jobs")


class TestJobCompiler(unittest.TestCase):
    def test_full_chain_primikey(self):
        path = os.path.join(JOBS_DIR, "primikey-usba", "scene-float-a.job.json")
        artifacts = job_compiler.compile_path(path, dry_run=True)
        self.assertEqual(artifacts["schema_version"], job_compiler.ARTIFACTS_SCHEMA)
        spec_validator.validate_product_spec(artifacts["product_spec"])
        spec_validator.validate_creative_spec(artifacts["creative_spec"])
        spec_validator.validate_execution_request(artifacts["execution_request"])
        spec_validator.validate_provider_prompt(artifacts["provider_prompt"])
        spec_validator.validate_provider_execution_plan(artifacts["execution_plan"])
        self.assertIn("graph", artifacts)
        self.assertEqual(artifacts["graph"]["schema_version"], "graph-v1")
        self.assertIn("geometry_text", artifacts["product_spec"])
        self.assertTrue(artifacts["product_spec"]["geometry_text"])

    def test_edit_spec_for_surgical_job(self):
        path = os.path.join(
            JOBS_DIR, "primikey-usba", "scene-float-a.job.json"
        )
        job = jobspec.load(path)
        job["spec"]["prompt_template"] = (
            "Lock scene.\n\nONLY CHANGE — logo:\n1. Remove swoosh.\n\n{cad}\n\nVIEW: {view}."
        )
        job["spec"]["negative"] = "no swoosh"
        artifacts = job_compiler.compile_job(job, dry_run=True)
        self.assertEqual(len(artifacts["edit_specs"]), 1)
        spec_validator.validate_edit_spec(artifacts["edit_specs"][0])
        self.assertIn("no swoosh", artifacts["provider_prompt"]["negative"] or "")


if __name__ == "__main__":
    unittest.main()
