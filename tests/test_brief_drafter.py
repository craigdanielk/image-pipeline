#!/usr/bin/env python3
"""Tests for brief_drafter — missing-key error path and system-context wiring.

No real network calls: the anthropic-package test injects a fake module via
sys.modules so it works whether or not the real package is installed.
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

import brief_drafter


class BriefDrafterTests(unittest.TestCase):
    def test_missing_api_key_raises_clear_error(self) -> None:
        prior = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with self.assertRaises(brief_drafter.BriefDraftError) as ctx:
                brief_drafter.draft_prompt_template(cad_text="geometry", brief_text="kid hero shot")
            self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))
        finally:
            if prior is not None:
                os.environ["ANTHROPIC_API_KEY"] = prior

    def test_system_context_reaches_the_api_call(self) -> None:
        captured = {}

        def fake_create(*, model, max_tokens, system, messages):
            captured["system"] = system
            block = types.SimpleNamespace(type="text", text="drafted prompt")
            return types.SimpleNamespace(content=[block])

        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.Anthropic = lambda api_key: types.SimpleNamespace(
            messages=types.SimpleNamespace(create=fake_create)
        )

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with mock.patch.dict(sys.modules, {"anthropic": fake_anthropic}):
                result = brief_drafter.draft_prompt_template(
                    cad_text="geometry",
                    brief_text="kid hero shot",
                    system_context="ENGINE CONSTRAINTS: nano_banana_2 rejects batch_size.",
                )

        self.assertEqual(result, "drafted prompt")
        self.assertIn("nano_banana_2 rejects batch_size", captured["system"])
        self.assertIn("preserve the literal placeholder tokens".lower(), captured["system"].lower())


if __name__ == "__main__":
    unittest.main()
