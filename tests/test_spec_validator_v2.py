#!/usr/bin/env python3
"""Tests for spec_validator_v2."""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import spec_validator_v2

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_EXAMPLES = os.path.join(_ROOT, "schemas", "examples")
_KNOWLEDGE = os.path.join(_ROOT, "knowledge")


def _load(name: str) -> dict:
    with open(name, encoding="utf-8") as f:
        return json.load(f)


class TestSpecValidatorV2(unittest.TestCase):
    def test_product_hairbands_valid(self) -> None:
        spec = _load(os.path.join(_KNOWLEDGE, "products", "hairbands.json"))
        self.assertTrue(spec_validator_v2.validate_product_spec_v2(spec))

    def test_creative_starz_kid_valid(self) -> None:
        spec = _load(os.path.join(_KNOWLEDGE, "campaigns", "starz-kid.json"))
        self.assertTrue(spec_validator_v2.validate_creative_spec_v2(spec))

    def test_edit_logo_fix_valid(self) -> None:
        spec = _load(os.path.join(_EXAMPLES, "edit-spec-v2-hairbands-logo-fix.json"))
        self.assertTrue(spec_validator_v2.validate_edit_spec_v2(spec))

    def test_bundle_cross_ref(self) -> None:
        product = _load(os.path.join(_KNOWLEDGE, "products", "hairbands.json"))
        creative = _load(os.path.join(_KNOWLEDGE, "campaigns", "starz-kid.json"))
        edit = _load(os.path.join(_EXAMPLES, "edit-spec-v2-hairbands-logo-fix.json"))
        self.assertTrue(
            spec_validator_v2.validate_spec_bundle_v2(product, creative, [edit])
        )

    def test_rejects_geometry_text(self) -> None:
        spec = _load(os.path.join(_KNOWLEDGE, "products", "hairbands.json"))
        spec["geometry_text"] = "blob"
        with self.assertRaises(spec_validator_v2.SpecV2Error):
            spec_validator_v2.validate_product_spec_v2(spec)

    def test_rejects_only_change_prose(self) -> None:
        spec = _load(os.path.join(_EXAMPLES, "edit-spec-v2-hairbands-logo-fix.json"))
        spec["operations"][0]["target"] = "ONLY CHANGE the logo"
        with self.assertRaises(spec_validator_v2.SpecV2Error):
            spec_validator_v2.validate_edit_spec_v2(spec)

    def test_rejects_prompt_template_on_creative(self) -> None:
        spec = _load(os.path.join(_KNOWLEDGE, "campaigns", "starz-kid.json"))
        spec["prompt_template"] = "Generate {cad}"
        with self.assertRaises(spec_validator_v2.SpecV2Error):
            spec_validator_v2.validate_creative_spec_v2(spec)


if __name__ == "__main__":
    unittest.main()
