"""Tests for POST /api/board/compile."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


class TestBoardCompile(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(server.app)

    def test_compile_with_two_operations_returns_chained_graph(self):
        res = self.client.post(
            "/api/board/compile",
            json={
                "product_slug": "hairbands",
                "campaign_id": "starz-kid-hairbands",
                "output_types": ["hero"],
                "operations": [
                    {"op": "replace_logo", "target": "chest_mark"},
                    {"op": "remove_background"},
                ],
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertIn("compile_dir", data)
        self.assertTrue(os.path.isdir(data["compile_dir"]))
        self.assertTrue(os.path.isfile(os.path.join(data["compile_dir"], "graph.json")))
        node_ids = [n["id"] for n in data["graph"]["nodes"]]
        self.assertTrue(any(nid.startswith("generate-0-replace_logo") for nid in node_ids))
        self.assertTrue(any(nid.startswith("generate-1-remove_background") for nid in node_ids))

    def test_compile_rejects_unknown_operation(self):
        res = self.client.post(
            "/api/board/compile",
            json={
                "product_slug": "hairbands",
                "campaign_id": "starz-kid-hairbands",
                "output_types": ["hero"],
                "operations": [{"op": "not_a_real_op"}],
            },
        )
        self.assertEqual(res.status_code, 400)

    def test_compile_rejects_unknown_product(self):
        res = self.client.post(
            "/api/board/compile",
            json={
                "product_slug": "does-not-exist",
                "campaign_id": "starz-kid-hairbands",
                "output_types": ["hero"],
                "operations": [],
            },
        )
        self.assertEqual(res.status_code, 404)

    def test_compile_rejects_path_traversal_tenant(self):
        res = self.client.post(
            "/api/board/compile",
            json={
                "product_slug": "hairbands",
                "campaign_id": "starz-kid-hairbands",
                "output_types": ["hero"],
                "operations": [],
                "tenant": "../../etc",
            },
        )
        self.assertEqual(res.status_code, 422, res.text)


class TestRuntimeChainAndWiring(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(server.app)

    def test_runtime_chain_lists_six_nodes_with_ports(self):
        res = self.client.get("/api/board/runtime-chain")
        self.assertEqual(res.status_code, 200, res.text)
        types = [n["node_type"] for n in res.json()["nodes"]]
        for expected in (
            "intake.load",
            "jobspec.validate",
            "prompt.build",
            "reference.resolve",
            "engine.generate",
            "output.persist",
            "artifacts.load",
        ):
            self.assertIn(expected, types)

    def test_validate_wiring_accepts_valid_ports(self):
        res = self.client.post(
            "/api/board/validate-wiring",
            json={
                "node_type": "engine.generate",
                "ports": {
                    "in": {"artifacts": {"type": "compile_artifacts"}},
                    "out": {"results": {"type": "engine_results"}},
                },
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["ok"])

    def test_validate_wiring_rejects_port_contract_violation(self):
        res = self.client.post(
            "/api/board/validate-wiring",
            json={"node_type": "engine.generate", "ports": {"in": {"artifacts": {"type": "prompt"}}}},
        )
        self.assertEqual(res.status_code, 400, res.text)

    def test_validate_wiring_rejects_undeclared_port(self):
        res = self.client.post(
            "/api/board/validate-wiring",
            json={"node_type": "prompt.build", "ports": {"out": {"bogus": {"type": "prompt"}}}},
        )
        self.assertEqual(res.status_code, 400, res.text)

    def test_artifacts_load_rejects_unknown_artifact(self):
        res = self.client.post("/api/board/artifacts-load", json={"artifact_id": "does-not-exist"})
        self.assertEqual(res.status_code, 404, res.text)


if __name__ == "__main__":
    unittest.main()
