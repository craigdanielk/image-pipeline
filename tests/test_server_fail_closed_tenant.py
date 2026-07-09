# tests/test_server_fail_closed_tenant.py
"""Contract tests: WRITE endpoints fail closed on missing tenant scope.

Every write endpoint (comments, approvals, artifact registry mutations,
knowledge product/campaign writes) MUST return HTTP 400 when it cannot resolve
an intended tenant — never silently default into the phantom "default"
workspace. These tests force the ambiguous case (two configured tenants, no
tenant param, no env pin) and assert 400 on each endpoint. A companion test
proves the single-tenant convenience path still resolves (no false 400).
"""
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

# Two tenants + no env pin => tenant scope is AMBIGUOUS => writes must 400.
_MULTI = [{"slug": "alpha", "label": "alpha"}, {"slug": "beta", "label": "beta"}]
_SOLO = [{"slug": "solo", "label": "solo"}]


def _ambiguous():
    """Context managers that make tenant resolution ambiguous."""
    return (
        mock.patch.object(server.tenants, "load_tenants", return_value=_MULTI),
        mock.patch.dict(server.os.environ, {"IMAGE_PIPELINE_WORKSPACE_ID": ""}, clear=False),
    )


class TestWriteEndpointsFailClosed(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(server.app)
        self._patches = _ambiguous()
        for p in self._patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    # --- approvals -------------------------------------------------------
    def test_asset_approval_missing_tenant_returns_400(self):
        res = self.client.patch("/api/assets/approval?path=out/x.png", json={"status": "approved"})
        self.assertEqual(res.status_code, 400)

    # --- artifact registry mutations ------------------------------------
    def test_artifact_sync_missing_tenant_returns_400(self):
        res = self.client.post("/api/artifacts/sync")
        self.assertEqual(res.status_code, 400)

    def test_artifact_comment_missing_tenant_returns_400(self):
        res = self.client.patch("/api/artifacts/some-id", json={"comment": "looks off"})
        self.assertEqual(res.status_code, 400)

    def test_artifact_delete_missing_tenant_returns_400(self):
        res = self.client.delete("/api/artifacts/some-id")
        self.assertEqual(res.status_code, 400)

    def test_artifact_approve_missing_tenant_returns_400(self):
        res = self.client.post("/api/artifacts/some-id/approve")
        self.assertEqual(res.status_code, 400)

    def test_artifact_restore_missing_tenant_returns_400(self):
        res = self.client.post("/api/artifacts/some-id/restore")
        self.assertEqual(res.status_code, 400)

    # --- knowledge writes ------------------------------------------------
    def test_knowledge_create_product_missing_tenant_returns_400(self):
        res = self.client.post(
            "/api/knowledge/products",
            json={"name": "Widget", "geometry_description": "a box"},
        )
        self.assertEqual(res.status_code, 400)

    def test_knowledge_update_product_missing_tenant_returns_400(self):
        res = self.client.put("/api/knowledge/products/widget", json={"name": "Widget"})
        self.assertEqual(res.status_code, 400)

    def test_knowledge_create_campaign_missing_tenant_returns_400(self):
        res = self.client.post(
            "/api/knowledge/campaigns",
            json={"product_spec_id": "widget", "label": "L", "scene_description": "s"},
        )
        self.assertEqual(res.status_code, 400)

    def test_knowledge_upload_ref_missing_tenant_returns_400(self):
        res = self.client.post(
            "/api/knowledge/products/widget/refs",
            files={"file": ("r.png", io.BytesIO(b"x"), "image/png")},
        )
        self.assertEqual(res.status_code, 400)

    # --- the error is explicit, not a generic failure -------------------
    def test_error_body_names_tenant_scope(self):
        res = self.client.post("/api/artifacts/some-id/approve")
        self.assertEqual(res.status_code, 400)
        self.assertIn("tenant", res.json()["detail"].lower())


class TestExplicitTenantBypassesGate(unittest.TestCase):
    """With an explicit ?tenant, the gate passes — a 400 must NOT come from
    tenant resolution (unknown artifact should 404, not 400)."""

    def setUp(self) -> None:
        self.client = TestClient(server.app)
        self._patches = _ambiguous()
        for p in self._patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    def test_explicit_tenant_reaches_handler_not_400(self):
        res = self.client.post("/api/artifacts/nope/approve?tenant=alpha")
        self.assertNotEqual(res.status_code, 400)
        self.assertEqual(res.status_code, 404)


class TestSingleTenantResolves(unittest.TestCase):
    """One configured tenant IS the workspace — no false 400."""

    def setUp(self) -> None:
        self.client = TestClient(server.app)
        p1 = mock.patch.object(server.tenants, "load_tenants", return_value=_SOLO)
        p2 = mock.patch.dict(server.os.environ, {"IMAGE_PIPELINE_WORKSPACE_ID": ""}, clear=False)
        self._patches = (p1, p2)
        for p in self._patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    def test_single_tenant_no_param_not_400(self):
        res = self.client.post("/api/artifacts/nope/approve")
        self.assertNotEqual(res.status_code, 400)
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
