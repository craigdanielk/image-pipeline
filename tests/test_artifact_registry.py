#!/usr/bin/env python3
"""Tests for artifact_registry discovery and feedback."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

import artifact_registry


class ArtifactRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.jobs = self.root / "jobs"
        self.jobs.mkdir()
        product = self.jobs / "hairbands" / "out"
        product.mkdir(parents=True)
        (product / "starz-hairbands-1.webp").write_bytes(b"fake")
        drive = self.jobs / "drive-upload-test" / "STARZ-Headband"
        drive.mkdir(parents=True)
        (drive / "starz-headband-2.webp").write_bytes(b"fake")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_discover_webp_and_sheet_import(self) -> None:
        found = artifact_registry.discover_images(self.jobs)
        slugs = {r["product_slug"] for r in found}
        self.assertIn("hairbands", slugs)
        self.assertIn("headband", slugs)
        sources = {r["source"] for r in found}
        self.assertIn("sheet_import", sources)

    def test_sync_and_soft_delete(self) -> None:
        stats = artifact_registry.sync_registry(self.root, self.jobs, "test")
        self.assertGreater(stats["added"], 0)
        rows = artifact_registry.list_artifacts(
            self.root, workspace_id="test", product_slug="hairbands", limit=10
        )
        self.assertGreaterEqual(len(rows), 1)
        aid = rows[0]["id"]
        artifact_registry.update_comment(self.root, aid, "needs logo fix", "test")
        artifact_registry.soft_delete(self.root, aid, reason="wrong crest", workspace_id="test")
        visible = artifact_registry.list_artifacts(
            self.root, workspace_id="test", product_slug="hairbands"
        )
        self.assertEqual(len(visible), 0)
        deleted = artifact_registry.list_artifacts(
            self.root,
            workspace_id="test",
            product_slug="hairbands",
            include_deleted=True,
        )
        self.assertEqual(len(deleted), 1)
        self.assertEqual(deleted[0]["delete_reason"], "wrong crest")

    def test_slug_from_filename_multi_word(self) -> None:
        self.assertEqual(
            artifact_registry._slug_from_filename("starz-ankle-weights-3.webp"),
            "ankle-weights",
        )


if __name__ == "__main__":
    unittest.main()
