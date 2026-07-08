"""result.as_ref must be a registered, valid node type."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import node_registry  # noqa: E402


class TestResultAsRefRegistered(unittest.TestCase):
    def test_result_as_ref_in_list_node_types(self):
        self.assertIn("result.as_ref", node_registry.list_node_types())

    def test_result_as_ref_spec_has_engine_results_in_and_image_path_out(self):
        spec = node_registry.get("result.as_ref")
        self.assertIn("results", spec.ports_in)
        self.assertEqual(spec.ports_in["results"].type, "engine_results")
        self.assertIn("refs", spec.ports_out)
        self.assertEqual(spec.ports_out["refs"].type, "image_path")


if __name__ == "__main__":
    unittest.main()
