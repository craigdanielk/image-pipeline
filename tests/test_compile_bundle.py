#!/usr/bin/env python3
"""Tests for compile_bundle — manifest contract and replay determinism."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import compile_bundle  # noqa: E402
import job_compiler  # noqa: E402

JOBS_DIR = os.path.join(os.path.dirname(__file__), "..", "jobs")


class TestCompileBundle(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="compile-bundle-")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _compile_primikey(self, subdir: str) -> dict:
        path = os.path.join(JOBS_DIR, "primikey-usba", "scene-float-a.job.json")
        compile_dir = os.path.join(self.tmp, subdir)
        return job_compiler.compile_path_to_dir(path, compile_dir, dry_run=True)

    def test_write_manifest_and_files(self) -> None:
        manifest = self._compile_primikey("one")
        self.assertEqual(manifest["schema_version"], compile_bundle.MANIFEST_SCHEMA)
        self.assertEqual(manifest["job_id"], "primikey-usba-scene-float-a")
        compile_dir = os.path.join(self.tmp, "one")
        for name in compile_bundle._COMPILER_FILES:
            self.assertTrue(os.path.isfile(os.path.join(compile_dir, name)))
        self.assertTrue(os.path.isfile(os.path.join(compile_dir, compile_bundle.FILE_MANIFEST)))

    def test_replay_identical_hashes(self) -> None:
        m1 = self._compile_primikey("a")
        m2 = self._compile_primikey("b")
        self.assertEqual(m1["compile_hash"], m2["compile_hash"])
        self.assertEqual(m1["manifest_hash"], m2["manifest_hash"])
        for filename in m1["artifacts"]:
            self.assertEqual(
                m1["artifacts"][filename]["content_hash"],
                m2["artifacts"][filename]["content_hash"],
            )

    def test_runtime_bundle_excludes_specs(self) -> None:
        self._compile_primikey("rt")
        compile_dir = os.path.join(self.tmp, "rt")
        runtime = compile_bundle.load_runtime_bundle(compile_dir)
        self.assertEqual(runtime["schema_version"], compile_bundle.RUNTIME_BUNDLE_SCHEMA)
        for key in ("product_spec", "creative_spec", "edit_specs", "provider_prompt"):
            self.assertNotIn(key, runtime)
        view = compile_bundle.runtime_artifacts_view(runtime)
        for key in ("product_spec", "creative_spec", "edit_specs", "provider_prompt"):
            self.assertNotIn(key, view)

    def test_manifest_hash_mismatch_raises(self) -> None:
        self._compile_primikey("bad")
        compile_dir = os.path.join(self.tmp, "bad")
        path = os.path.join(compile_dir, compile_bundle.FILE_GRAPH)
        with open(path, encoding="utf-8") as f:
            data = f.read()
        with open(path, "w", encoding="utf-8") as f:
            f.write(data.replace("load", "loadx"))
        with self.assertRaises(compile_bundle.CompileBundleError):
            compile_bundle.load_manifest(compile_dir)


if __name__ == "__main__":
    unittest.main()
