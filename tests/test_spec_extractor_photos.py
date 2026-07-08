"""Tests for core/spec_extractor.extract_product_spec_from_photos — mocked SDK, no live LLM call."""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import spec_extractor  # noqa: E402


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


async def _fake_query_ok(*, prompt, options):  # noqa: ANN001
    yield _FakeMessage(
        '{"schema_version": "product-spec-v2", "id": "shin-pad-sleeve", "name": "Shin Pad Sleeve", '
        '"product": {"geometry": {"description": "Black compression sleeve worn over the shin"}}, '
        '"constraints": {"preserve_geometry": true, "preserve_material": true, "preserve_proportions": true}, '
        '"editing": {"protected_regions": ["sleeve"], "editable_regions": ["background"]}}'
    )


async def _fake_query_fenced(*, prompt, options):  # noqa: ANN001
    yield _FakeMessage('```json\n{"schema_version": "product-spec-v2", "id": "x", "name": "X"}\n```')


class TestExtractProductSpecFromPhotos(unittest.IsolatedAsyncioTestCase):
    async def test_parses_json_draft_from_model_output(self):
        fake_sdk = mock.MagicMock()
        fake_sdk.query = _fake_query_ok
        fake_sdk.ClaudeAgentOptions = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk}):
            draft = await spec_extractor.extract_product_spec_from_photos(
                ["/tmp/flatlay.jpg"], name_hint="Shin Pad Sleeve"
            )
        self.assertEqual(draft["schema_version"], "product-spec-v2")
        self.assertEqual(draft["id"], "shin-pad-sleeve")
        self.assertIn("sleeve", draft["product"]["geometry"]["description"].lower())

    async def test_strips_markdown_fences(self):
        fake_sdk = mock.MagicMock()
        fake_sdk.query = _fake_query_fenced
        fake_sdk.ClaudeAgentOptions = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk}):
            draft = await spec_extractor.extract_product_spec_from_photos(["/tmp/a.jpg"])
        self.assertEqual(draft["id"], "x")

    async def test_raises_on_non_json_output(self):
        async def fake_query_bad(*, prompt, options):  # noqa: ANN001
            yield _FakeMessage("not json at all")

        fake_sdk = mock.MagicMock()
        fake_sdk.query = fake_query_bad
        fake_sdk.ClaudeAgentOptions = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk}):
            with self.assertRaises(spec_extractor.SpecExtractError):
                await spec_extractor.extract_product_spec_from_photos(["/tmp/a.jpg"])


if __name__ == "__main__":
    unittest.main()
