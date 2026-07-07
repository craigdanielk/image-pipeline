#!/usr/bin/env python3
"""Tests for knowledge_store."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import knowledge_store


class KnowledgeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "knowledge" / "products").mkdir(parents=True)
        (self.root / "knowledge" / "campaigns").mkdir(parents=True)
        self.store = knowledge_store.KnowledgeStore(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_create_product_and_campaign(self) -> None:
        p = self.store.create_product(
            {
                "id": "test-brand",
                "name": "Test Brand",
                "geometry_description": "A test product shape",
            }
        )
        self.assertEqual(p["slug"], "test-brand")
        c = self.store.create_campaign(
            {
                "id": "launch",
                "product_spec_id": "test-brand",
                "label": "Launch",
                "scene_description": "Studio hero scene",
            }
        )
        self.assertEqual(c["product_spec_id"], "test-brand")
        loaded = self.store.get_product("test-brand")
        self.assertEqual(loaded["name"], "Test Brand")


if __name__ == "__main__":
    unittest.main()
