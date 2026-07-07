#!/usr/bin/env python3
"""Tests for core/spec_extractor.py — cad.md → ProductSpec."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import spec_extractor  # noqa: E402
import spec_validator  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
PRIMIKEY_CAD = os.path.join(ROOT, "jobs", "primikey-usba", "cad.md")
SCENE_CAD = os.path.join(ROOT, "schemas", "examples", "cad-with-scene-locks.md")


class TestSpecExtractor(unittest.TestCase):
    def test_extracts_engineering_cad_product_spec(self):
        self.assertTrue(os.path.exists(PRIMIKEY_CAD))
        product = spec_extractor.extract_product_spec(PRIMIKEY_CAD)
        spec_validator.validate_product_spec(product)
        self.assertEqual(product["schema_version"], "product-spec-v1")
        self.assertTrue(product["immutable"])
        self.assertIn("usb", product["geometry_text"].lower())

    def test_extracts_scene_locks_and_run_invariants(self):
        self.assertTrue(os.path.exists(SCENE_CAD))
        product = spec_extractor.extract_product_spec(SCENE_CAD)
        creative = spec_extractor.extract_creative_from_cad(
            SCENE_CAD,
            product_spec_id=product["id"],
            prompt_template="Render product. Geometry:\n\n{cad}",
        )
        spec_validator.validate_creative_spec(creative)
        self.assertGreaterEqual(len(creative["scene_locks"]), 1)
        self.assertGreater(len(creative["run_invariants"]), 0)

    def test_deterministic_extraction(self):
        a = spec_extractor.extract_product_spec(PRIMIKEY_CAD)
        b = spec_extractor.extract_product_spec(PRIMIKEY_CAD)
        self.assertEqual(a["geometry_text"], b["geometry_text"])
        self.assertEqual(a["source"]["content_hash"], b["source"]["content_hash"])


if __name__ == "__main__":
    unittest.main()
