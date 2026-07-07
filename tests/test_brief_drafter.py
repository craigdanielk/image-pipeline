#!/usr/bin/env python3
"""Tests for brief_drafter — the missing-key error path (no network calls)."""
from __future__ import annotations

import os
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
