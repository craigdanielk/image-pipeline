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

    def test_discover_nested_campaign_products(self) -> None:
        # jobs/<campaign>/<product>/out/ — a real per-product folder two levels deep.
        nested = self.jobs / "starz-kid-images" / "ear-plugs" / "out"
        nested.mkdir(parents=True)
        (nested / "ear-plugs-shot-1__hero__v1.png").write_bytes(b"fake")

        # jobs/<campaign>/out/ — a campaign-level catch-all with curated filenames.
        catchall = self.jobs / "starz-kid-images" / "out"
        catchall.mkdir(parents=True)
        (catchall / "starz-ankle-weights-2.webp").write_bytes(b"fake")

        found = artifact_registry.discover_images(self.jobs)
        by_slug = {r["product_slug"]: r["name"] for r in found}
        self.assertEqual(by_slug.get("ear-plugs"), "ear-plugs-shot-1__hero__v1.png")
        self.assertEqual(by_slug.get("ankle-weights"), "starz-ankle-weights-2.webp")
        # campaign container name itself must never surface as a product slug
        self.assertNotIn("starz-kid-images", by_slug)

    def test_approve_and_restore(self) -> None:
        artifact_registry.sync_registry(self.root, self.jobs, "test")
        rows = artifact_registry.list_artifacts(self.root, workspace_id="test", product_slug="hairbands")
        aid = rows[0]["id"]
        self.assertEqual(rows[0]["status"], "pending")

        approved = artifact_registry.approve_artifact(self.root, aid, workspace_id="test")
        self.assertEqual(approved["status"], "approved")

        rejected = artifact_registry.soft_delete(self.root, aid, reason="nope", workspace_id="test")
        self.assertEqual(rejected["status"], "rejected")
        self.assertIsNone(rejected["approved_at"])

        restored = artifact_registry.restore_artifact(self.root, aid, workspace_id="test")
        self.assertEqual(restored["status"], "pending")

    def test_status_filter_and_pagination(self) -> None:
        artifact_registry.sync_registry(self.root, self.jobs, "test")
        all_rows = artifact_registry.list_artifacts(self.root, workspace_id="test")
        self.assertGreaterEqual(len(all_rows), 2)
        aid = all_rows[0]["id"]
        artifact_registry.approve_artifact(self.root, aid, workspace_id="test")

        approved = artifact_registry.list_artifacts(self.root, workspace_id="test", status="approved")
        self.assertEqual([r["id"] for r in approved], [aid])

        total = artifact_registry.count_artifacts(self.root, workspace_id="test")
        self.assertEqual(total, len(all_rows))

        page1 = artifact_registry.list_artifacts(self.root, workspace_id="test", limit=1, offset=0)
        page2 = artifact_registry.list_artifacts(self.root, workspace_id="test", limit=1, offset=1)
        self.assertEqual(len(page1), 1)
        self.assertNotEqual(page1[0]["id"], page2[0]["id"])

    def test_group_key_collapses_version_suffix(self) -> None:
        self.assertEqual(
            artifact_registry._group_key("hairbands-shot-4-logo-fix-v3__kid-turf-wide__v1.png"),
            "hairbands-shot-4-logo-fix",
        )
        self.assertEqual(
            artifact_registry._group_key("hairbands-shot-4-logo-fix__kid-turf-wide__v1.png"),
            "hairbands-shot-4-logo-fix",
        )
        self.assertEqual(
            artifact_registry._group_key("starz-hairbands-2.webp"),
            "starz-hairbands-2",
        )


if __name__ == "__main__":
    unittest.main()
