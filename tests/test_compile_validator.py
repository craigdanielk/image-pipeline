#!/usr/bin/env python3
"""Tests for compile_validator gate."""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import compile_validator

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_EXAMPLES = os.path.join(_ROOT, "schemas", "examples")
_KNOWLEDGE = os.path.join(_ROOT, "knowledge")


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestCompileValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.product = _load(os.path.join(_KNOWLEDGE, "products", "hairbands.json"))
        self.creative = _load(os.path.join(_KNOWLEDGE, "campaigns", "starz-kid.json"))
        self.edit = _load(os.path.join(_EXAMPLES, "edit-spec-v2-hairbands-logo-fix.json"))
        self.execution = _load(
            os.path.join(_EXAMPLES, "execution-request-hairbands-logo-fix.json")
        )

    def test_v2_lane_passes(self) -> None:
        lane = compile_validator.validate_for_compile(
            self.product,
            self.creative,
            self.execution,
            edit_specs=[self.edit],
        )
        self.assertEqual(lane, "v2")

    def test_v2_edit_schema_mismatch_fails(self) -> None:
        bad_edit = dict(self.edit)
        bad_edit["schema_version"] = "edit-spec-v1"
        with self.assertRaises(compile_validator.CompileGateError):
            compile_validator.validate_for_compile(
                self.product,
                self.creative,
                self.execution,
                edit_specs=[bad_edit],
            )


if __name__ == "__main__":
    unittest.main()
