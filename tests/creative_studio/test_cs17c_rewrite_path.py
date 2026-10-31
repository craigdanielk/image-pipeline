"""CS-17c: tolerate only the `path` query Vercel's rewrite injects; stay strict otherwise."""
from __future__ import annotations

import io
import json
import unittest
from urllib.parse import urlencode

from service.api import CreativeStudioApi, Request
from service.errors import ServiceError

WS = "11111111-1111-4111-8111-111111111111"


def _req(path, query, method="GET"):
    return Request({"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query,
                    "wsgi.input": io.BytesIO(b"")})


class _Probe(CreativeStudioApi):
    def __init__(self):
        pass

    def _assert_gateway(self, *a):
        pass

    def health(self, req):
        return 200, {"ok": True}

    readiness = health
    my_workspaces = health


def _dispatch(path, query):
    return _Probe().dispatch(_req(path, query))


def _refused(testcase, path, query):
    with testcase.assertRaises(ServiceError) as cm:
        _dispatch(path, query)
    testcase.assertEqual((cm.exception.status, cm.exception.code), (400, "unsupported_parameter"))
    return cm.exception.detail


class RewriteInjectedPath(unittest.TestCase):
    def test_injected_path_accepted_for_route_families(self):
        for path in ("/api/v1/health", "/api/v1/readiness", "/api/v1/me/workspaces"):
            q = urlencode({"path": path[len("/api/v1/"):]})
            self.assertEqual(_dispatch(path, q)[0], 200, path)

    def test_workspace_scoped_with_after_limit(self):
        path = f"/api/v1/workspaces/{WS}/runs"
        q = urlencode({"after": "3", "limit": "10", "path": f"workspaces/{WS}/runs"})
        req = _req(path, q)
        api = _Probe()
        self.assertTrue(api._rewrite_injected_path(req))
        # passes the parameter gate: failure (if any) is not unsupported_parameter
        try:
            api.dispatch(req)
        except ServiceError as err:
            self.assertNotEqual(err.code, "unsupported_parameter")
        except Exception:
            pass  # handler reached (no DB in this unit test)

    def test_spoofed_path_refused(self):
        self.assertEqual(_refused(self, "/api/v1/health", "path=other"), "path")

    def test_unknown_key_refused_even_with_injected_path(self):
        self.assertEqual(_refused(self, "/api/v1/health", "path=health&zz=1"), "zz")
        self.assertEqual(_refused(self, "/api/v1/health", "zz=1"), "zz")

    def test_duplicate_path_refused(self):
        self.assertEqual(_refused(self, "/api/v1/health", "path=health&path=health"), "path")
        self.assertEqual(_refused(self, "/api/v1/health", "path=other&path=health"), "path")

    def test_bare_path_without_value_match_refused(self):
        self.assertEqual(_refused(self, "/api/v1/health", "path="), "path")

    def test_worker_route_has_no_capture(self):
        cfg = json.load(open("vercel.json"))
        rw = {r["source"]: r["destination"] for r in cfg["rewrites"]}
        self.assertNotIn(":", rw["/api/internal/worker"].replace("/api/worker.py", ""))
        self.assertNotIn(":", "/api/internal/worker")


if __name__ == "__main__":
    unittest.main()
