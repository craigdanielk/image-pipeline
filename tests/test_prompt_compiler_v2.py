#!/usr/bin/env python3
"""Golden test — hairbands logo-fix v2 specs compile without prose."""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import prompt_compiler_v2
import spec_validator

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_EXAMPLES = os.path.join(_ROOT, "schemas", "examples")
_KNOWLEDGE = os.path.join(_ROOT, "knowledge")


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestPromptCompilerV2(unittest.TestCase):
    def test_hairbands_logo_fix_golden(self) -> None:
        product = _load(os.path.join(_KNOWLEDGE, "products", "hairbands.json"))
        creative = _load(os.path.join(_KNOWLEDGE, "campaigns", "starz-kid.json"))
        edit = _load(os.path.join(_EXAMPLES, "edit-spec-v2-hairbands-logo-fix.json"))
        execution = _load(
            os.path.join(_EXAMPLES, "execution-request-hairbands-logo-fix.json")
        )

        provider = prompt_compiler_v2.compile_v2(
            product, creative, execution, edit_specs=[edit]
        )

        self.assertEqual(provider["schema_version"], spec_validator.PROVIDER_SCHEMA)
        self.assertIn("chest_sternum", provider["positive"])
        self.assertIn("Replace the logo", provider["positive"])
        self.assertIn("joma j", provider["negative"] or "")
        self.assertEqual(provider["metadata"]["compiler"], "prompt-compiler-v2")
        self.assertEqual(provider["metadata"]["spec_lane"], "v2")
        self.assertTrue(provider["metadata"]["content_hash"])

    def test_deterministic_hash(self) -> None:
        product = _load(os.path.join(_KNOWLEDGE, "products", "hairbands.json"))
        creative = _load(os.path.join(_KNOWLEDGE, "campaigns", "starz-kid.json"))
        edit = _load(os.path.join(_EXAMPLES, "edit-spec-v2-hairbands-logo-fix.json"))
        execution = _load(
            os.path.join(_EXAMPLES, "execution-request-hairbands-logo-fix.json")
        )
        a = prompt_compiler_v2.compile_v2(product, creative, execution, edit_specs=[edit])
        b = prompt_compiler_v2.compile_v2(product, creative, execution, edit_specs=[edit])
        self.assertEqual(
            a["metadata"]["content_hash"],
            b["metadata"]["content_hash"],
        )


if __name__ == "__main__":
    unittest.main()
