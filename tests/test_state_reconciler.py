#!/usr/bin/env python3
"""Tests for the state reconciler and its no-silent-hide guarantee.

Covers the brief acceptance criteria:
  - Moving a file on disk without updating the registry produces a VISIBLE
    broken row + a divergence-report entry within one reconcile cycle.
  - A Drive upload with no registry row appears in the unlinked report.
  - Zero silent hides: UI row count equals registry row count.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import artifact_registry
import state_reconciler


class StateReconcilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.jobs = self.root / "jobs"
        (self.jobs / "hairbands" / "out").mkdir(parents=True)
        self.img = self.jobs / "hairbands" / "out" / "starz-hairbands-1.webp"
        self.img.write_bytes(b"fake")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_moving_file_flags_broken_and_reports(self) -> None:
        # Sync so the disk image gets a registry row.
        artifact_registry.sync_registry(self.root, self.jobs)
        before = artifact_registry.count_artifacts(self.root)
        self.assertEqual(before, 1)

        # Disk sweep moves the file WITHOUT updating the registry path.
        self.img.rename(self.img.parent / "moved-elsewhere.webp")

        report = artifact_registry.reconcile(self.root, self.jobs, sync_first=False)

        # AC: visible broken row + divergence-report entry within one cycle.
        self.assertEqual(report["counts"]["broken"], 1)
        self.assertEqual(len(report["broken"]), 1)
        self.assertGreaterEqual(report["divergence"], 1)

        rows = artifact_registry.list_artifacts(self.root, include_deleted=False)
        self.assertEqual(len(rows), 1, "broken row must stay visible, not be hidden")
        self.assertTrue(rows[0]["broken"])

        # And it is reachable via the dedicated broken filter.
        broken = artifact_registry.list_artifacts(self.root, status="broken")
        self.assertEqual(len(broken), 1)

    def test_unlinked_drive_upload_reported(self) -> None:
        # A Drive-uploaded webp with NO registry row.
        drive = self.jobs / "drive-upload-x" / "starz-headband"
        drive.mkdir(parents=True)
        (drive / "starz-headband-9.webp").write_bytes(b"fake")

        # Reconcile with an EMPTY registry (no sync) so the drive file is unlinked.
        registry = {"artifacts": {}}
        report = state_reconciler.reconcile_registry(registry, self.jobs, now=1.0)
        paths = {e["path"] for e in report["unlinked"]}
        self.assertTrue(any("starz-headband-9.webp" in p for p in paths))
        self.assertGreaterEqual(report["counts"]["unlinked"], 1)
        self.assertTrue(any(e["source"] == "drive" for e in report["unlinked"]))

    def test_orphaned_row_flagged(self) -> None:
        registry = {"artifacts": {"a1": {"path": "", "name": "ghost.png"}}}
        report = state_reconciler.reconcile_registry(registry, self.jobs, now=1.0)
        self.assertEqual(report["counts"]["orphaned"], 1)
        self.assertTrue(registry["artifacts"]["a1"]["broken"])

    def test_zero_silent_hides_count_matches(self) -> None:
        artifact_registry.sync_registry(self.root, self.jobs)
        # Break the file.
        self.img.unlink()
        artifact_registry.reconcile(self.root, self.jobs, sync_first=False)
        # UI row count (list) == registry row count (count) — nothing hidden.
        listed = artifact_registry.list_artifacts(self.root, limit=100)
        counted = artifact_registry.count_artifacts(self.root)
        raw = artifact_registry.load_registry(self.root)["artifacts"]
        self.assertEqual(len(listed), counted)
        self.assertEqual(counted, len(raw))

    def test_divergence_report_written(self) -> None:
        artifact_registry.sync_registry(self.root, self.jobs)
        report = artifact_registry.reconcile(self.root, self.jobs, sync_first=False)
        self.assertTrue(report.get("report_path"))
        self.assertTrue(Path(report["report_path"]).is_file())
        latest = Path(report["report_path"]).parent / "divergence-latest.json"
        self.assertTrue(latest.is_file())


if __name__ == "__main__":
    unittest.main()
