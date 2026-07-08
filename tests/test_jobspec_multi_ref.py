#!/usr/bin/env python3
"""Tests for multi-reference JobSpec support (canonical, tenant-agnostic)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import graph_compiler  # noqa: E402
import jobspec  # noqa: E402
import adapters.reference_local as reference_local  # noqa: E402

JOBS_DIR = os.path.join(os.path.dirname(__file__), "..", "jobs")


class TestMultiReference(unittest.TestCase):
    def test_jobspec_loads_multi_ref_example(self):
        path = os.path.join(JOBS_DIR, "_example-multi-ref.job.json")
        job = jobspec.load(path)
        self.assertIsInstance(job["reference"], list)
        self.assertEqual(len(job["reference"]), 3)

    def test_reference_local_resolves_list(self):
        # Self-contained: the example job's plate PNGs live under a gitignored
        # reference/ dir, so a fresh clone/worktree has no binary assets.
        # Exercise list resolution against temp fixtures instead.
        with tempfile.TemporaryDirectory() as job_dir:
            names = ["plate-1.png", "plate-2.png", "plate-3.png"]
            for name in names:
                with open(os.path.join(job_dir, name), "wb") as f:
                    f.write(b"\x89PNG stub")
            refs = reference_local.resolve(
                [names[0], {"image": names[1]}, names[2]], job_dir
            )
            self.assertIsInstance(refs, list)
            self.assertEqual(len(refs), 3)
            for path in refs:
                self.assertTrue(os.path.exists(path), path)

    def test_graph_compiler_lowers_multi_ref(self):
        path = os.path.join(JOBS_DIR, "_example-multi-ref.job.json")
        graph = graph_compiler.compile_path(path)
        gen = next(n for n in graph["nodes"] if n["id"] == "generate")
        self.assertEqual(gen["ports"]["in"]["refs"]["type"], "image_path_list")

    def test_rejects_invalid_reference_list_item(self):
        with self.assertRaises(jobspec.JobSpecError):
            jobspec.validate(
                {
                    "schema_version": "job-v1",
                    "id": "bad-ref",
                    "spec": {"prompt_template": "x"},
                    "reference": [123],
                    "engine": {"name": "higgsfield", "model": "nano_banana_2"},
                }
            )


if __name__ == "__main__":
    unittest.main()
