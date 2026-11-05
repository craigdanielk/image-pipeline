"""Shot-spec compile branch (creative catalogue) and the legacy-prose regression guard."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from service.requests import compile_mapping

LEGACY = Path(__file__).parent / "fixtures" / "legacy_compile_b904f16.json"


class LegacyCompileTests(unittest.TestCase):
    def test_legacy_concept_direction_unchanged(self):
        """A request without shot_spec compiles byte-identically to output captured before the shot_spec branch."""
        fx = json.loads(LEGACY.read_text())
        out = compile_mapping.compile_inputs(fx["request"], fx["treatment"], fx["package"])
        self.assertEqual(out, fx["expected"])
        self.assertNotIn("shot_spec", fx["request"])


if __name__ == "__main__":
    unittest.main()
