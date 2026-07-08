# tests/test_server_extract_endpoint.py
"""Tests for POST /api/knowledge/products/extract — mocked spec_extractor, no live LLM call."""
from __future__ import annotations

import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
import spec_extractor  # noqa: E402


class TestExtractEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(server.app)

    def test_extract_returns_draft_from_photos(self):
        fake_draft = {
            "schema_version": "product-spec-v2",
            "id": "shin-pad-sleeve",
            "name": "Shin Pad Sleeve",
        }

        async def fake_extract(photo_paths, *, name_hint=None, notes=None):
            self.assertTrue(len(photo_paths) == 1)
            self.assertTrue(os.path.isfile(photo_paths[0]))
            return fake_draft

        with mock.patch.object(spec_extractor, "extract_product_spec_from_photos", fake_extract):
            with mock.patch.object(server, "spec_extractor", spec_extractor):
                res = self.client.post(
                    "/api/knowledge/products/extract",
                    data={"name": "Shin Pad Sleeve", "notes": ""},
                    files={"photos": ("flatlay.jpg", io.BytesIO(b"fake-bytes"), "image/jpeg")},
                )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["draft"], fake_draft)

    def test_extract_requires_at_least_one_photo(self):
        res = self.client.post("/api/knowledge/products/extract", data={"name": "X"}, files={})
        self.assertEqual(res.status_code, 422)  # FastAPI rejects missing required File(...) field

    def test_extract_maps_spec_extract_error_to_422(self):
        async def fake_extract_raises(photo_paths, *, name_hint=None, notes=None):
            raise spec_extractor.SpecExtractError("model did not return JSON")

        with mock.patch.object(spec_extractor, "extract_product_spec_from_photos", fake_extract_raises):
            with mock.patch.object(server, "spec_extractor", spec_extractor):
                res = self.client.post(
                    "/api/knowledge/products/extract",
                    data={},
                    files={"photos": ("a.jpg", io.BytesIO(b"x"), "image/jpeg")},
                )
        self.assertEqual(res.status_code, 422)


if __name__ == "__main__":
    unittest.main()
