#!/usr/bin/env python3
"""Tests for brief_drafter — missing-package error path and system-context wiring.

No real subprocess/CLI calls: the claude_agent_sdk test injects a fake module
via sys.modules so it works whether or not the real package is installed.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

import brief_drafter


class BriefDrafterTests(unittest.TestCase):
    def test_missing_sdk_raises_clear_error(self) -> None:
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": None}):
            with self.assertRaises(brief_drafter.BriefDraftError) as ctx:
                asyncio.run(
                    brief_drafter.draft_prompt_template(cad_text="geometry", brief_text="kid hero shot")
                )
        self.assertIn("claude-agent-sdk", str(ctx.exception))

    def test_system_context_reaches_the_sdk_call(self) -> None:
        captured = {}

        async def fake_query(*, prompt, options):
            captured["system_prompt"] = options.system_prompt
            captured["prompt"] = prompt
            block = types.SimpleNamespace(text="drafted prompt")
            message = types.SimpleNamespace(content=[block])
            yield message

        fake_sdk = types.ModuleType("claude_agent_sdk")
        fake_sdk.query = fake_query
        fake_sdk.ClaudeAgentOptions = lambda **kwargs: types.SimpleNamespace(**kwargs)

        with mock.patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk}):
            result = asyncio.run(
                brief_drafter.draft_prompt_template(
                    cad_text="geometry",
                    brief_text="kid hero shot",
                    system_context="ENGINE CONSTRAINTS: nano_banana_2 rejects batch_size.",
                )
            )

        self.assertEqual(result, "drafted prompt")
        self.assertIn("nano_banana_2 rejects batch_size", captured["system_prompt"])
        self.assertIn("preserve the literal placeholder tokens".lower(), captured["system_prompt"].lower())
        self.assertIn("kid hero shot", captured["prompt"])


if __name__ == "__main__":
    unittest.main()
