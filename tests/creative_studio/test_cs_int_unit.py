"""CS-INT: integrated hosted API surface (no database)."""
from __future__ import annotations

import re
import unittest
import uuid

from service import api

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
