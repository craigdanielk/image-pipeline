#!/usr/bin/env python3
"""Tests for knowledge_loader."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import knowledge_loader


class TestKnowledgeLoader(unittest.TestCase):
    def test_load_product(self) -> None:
        spec = knowledge_loader.load_product("hairbands")
        self.assertEqual(spec["id"], "hairbands")
        self.assertEqual(spec["schema_version"], "product-spec-v2")

    def test_load_operation(self) -> None:
        op = knowledge_loader.load_operation("replace_logo")
        self.assertEqual(op["id"], "replace_logo")
        self.assertIn("prompt_fragment", op)

    def test_list_operations(self) -> None:
        ops = knowledge_loader.list_operations()
        self.assertIn("replace_logo", ops)
        self.assertIn("remove_element", ops)


if __name__ == "__main__":
    unittest.main()
