#!/usr/bin/env python3
"""Tests for the tenant registry."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

import tenants


class TenantsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_seed_discovers_consumer_workspaces(self) -> None:
        base = self.root / "tenants_base"
        (base / "joga" / "tools" / "image-pipeline" / "jobs").mkdir(parents=True)
        (base / "acme" / "image-pipeline" / "jobs").mkdir(parents=True)
        (base / "no-pipeline-here").mkdir(parents=True)

        os.environ["IMAGE_PIPELINE_TENANTS_BASE"] = str(base)
        try:
            found = tenants.load_tenants(self.root)
        finally:
            del os.environ["IMAGE_PIPELINE_TENANTS_BASE"]

        slugs = {t["slug"] for t in found}
        self.assertEqual(slugs, {"joga", "acme"})
        self.assertTrue((self.root / "config" / "tenants.json").is_file())

    def test_seed_runs_once_then_config_is_authoritative(self) -> None:
        os.environ["IMAGE_PIPELINE_TENANTS_BASE"] = str(self.root / "empty")
        try:
            tenants.load_tenants(self.root)
        finally:
            del os.environ["IMAGE_PIPELINE_TENANTS_BASE"]

        tenants.add_tenant(self.root, "joga", str(self.root / "jobs"), label="JOGA")
        found = tenants.load_tenants(self.root)
        self.assertEqual([t["slug"] for t in found], ["joga"])

    def test_resolve_jobs_root_falls_back_when_unknown(self) -> None:
        fallback = self.root / "fallback-jobs"
        result = tenants.resolve_jobs_root(self.root, "nonexistent", fallback)
        self.assertEqual(result, fallback)

    def test_resolve_jobs_root_uses_registered_tenant(self) -> None:
        jobs_dir = self.root / "joga-jobs"
        jobs_dir.mkdir()
        tenants.add_tenant(self.root, "joga", str(jobs_dir))
        fallback = self.root / "fallback-jobs"
        result = tenants.resolve_jobs_root(self.root, "joga", fallback)
        self.assertEqual(result, jobs_dir)


if __name__ == "__main__":
    unittest.main()
