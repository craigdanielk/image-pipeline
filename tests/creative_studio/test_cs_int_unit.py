"""CS-INT: integrated hosted API surface (no database)."""
from __future__ import annotations

import json
import re
import unittest
import uuid
from pathlib import Path

from service import api

ROOT = Path(__file__).resolve().parents[2]

_GROUP = re.compile(r"\(([^()]*)\)")


def _sample_path(pattern: str) -> str:
    """Fill each capture group with a value of its own shape (uuid or slug)."""
    def fill(m):
        return str(uuid.uuid4()) if "{36}" in m.group(1) else "n1"
    return _GROUP.sub(fill, pattern.removeprefix("^").removesuffix("$"))


class IntegratedRouteTable(unittest.TestCase):
    def test_every_domain_module_registered_once_in_order(self):
        self.assertEqual([m.__name__ for m in api.DOMAIN_ROUTE_MODULES],
                         ["service.brand.routes", "service.requests.routes", "service.runtime.routes",
                          "service.composition.routes"])
        names = [n for _, _, n in api._ROUTES]
        self.assertEqual(len(names), len(set(names)), "one handler per name")
        keys = [(m, p.pattern) for m, p, _ in api._ROUTES]
        self.assertEqual(len(keys), len(set(keys)), "one handler per method + pattern")
        for prefix in ("b2_", "cs_b3_", "cs_b4_"):
            self.assertTrue(any(n.startswith(prefix) for n in names), f"{prefix} routes registered")

    def test_every_route_resolves_to_its_own_handler(self):
        """No earlier pattern shadows a later route (dispatch is first-match per method)."""
        for method, pattern, name in api._ROUTES:
            path = _sample_path(pattern.pattern)
            self.assertTrue(pattern.match(path), (name, path))
            first = next(n for m, p, n in api._ROUTES if m == method and p.match(path))
            self.assertEqual(first, name, f"{method} {path} is shadowed by {first}")
            self.assertTrue(callable(getattr(api.CreativeStudioApi, name)), name)

    def test_b1_surface_not_replaced(self):
        for attr in ("dispatch", "_tx", "_require_read", "health", "my_workspaces", "list_runs", "get_run"):
            self.assertEqual(getattr(api.CreativeStudioApi, attr).__module__, "service.api", attr)

    def test_reason_phrases_for_domain_statuses(self):
        self.assertEqual(api._REASONS[202], "Accepted")
        self.assertEqual(api._REASONS[428], "Precondition Required")


class PinnedRuntime(unittest.TestCase):
    """F3: the environment the suite runs in is the one service/requirements.txt describes."""

    def requirement_lines(self):
        return [l.split("#")[0].strip() for l in (ROOT / "service/requirements.txt").read_text().splitlines()
                if l.split("#")[0].strip()]

    def test_render_stack_pin_matches_goldens_and_runtime(self):
        import PIL
        from service.composition.renderer import renderer_version
        pins = [l for l in self.requirement_lines() if l.lower().startswith("pillow")]
        self.assertEqual(len(pins), 1, pins)
        self.assertRegex(pins[0], r"^Pillow==\d+\.\d+\.\d+$", "render stack is pinned exactly")
        self.assertIn("--only-binary Pillow", self.requirement_lines())
        pinned = pins[0].split("==")[1]
        self.assertEqual(PIL.__version__, pinned, "installed Pillow != pinned Pillow")
        golden = json.loads((ROOT / "tests/creative_studio/b4_goldens/goldens.json").read_text())
        self.assertEqual(golden["renderer_version"], renderer_version())
        self.assertIn(f" pillow/{pinned} ", golden["renderer_version"])

    def test_contract_runtime_dependencies_declared(self):
        lines = " ".join(self.requirement_lines())
        for dep in ("jsonschema[format-nongpl]>=4.18", "referencing", "cryptography", "psycopg2-binary", "PyJWT"):
            self.assertIn(dep, lines)

    def test_contract_formats_are_enforced(self):
        """Without the format extras jsonschema silently accepts any string for these formats."""
        from jsonschema import FormatChecker
        fc = FormatChecker()
        for fmt, bad, good in (("date-time", "31/10/2026", "2026-10-31T07:00:00Z"),
                               ("uri", "not a uri", "https://example-studio.test/a"),
                               ("uuid", "nope", str(uuid.uuid4()))):
            self.assertFalse(fc.conforms(bad, fmt), fmt)
            self.assertTrue(fc.conforms(good, fmt), fmt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
