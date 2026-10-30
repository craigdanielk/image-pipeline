#!/usr/bin/env python3
"""Tests for core/adapters/engine_higgsfield_marketing.py — zero network, zero subprocess.

Every test runs under a guard that makes socket connects, DNS lookups, urllib
opens and subprocess spawns raise. Provider responses come from recorded-shaped
fixtures in tests/fixtures/higgsfield_marketing/ via a mock transport.
"""
from __future__ import annotations

import copy
import json
import os
import socket
import subprocess
import sys
import unittest
import urllib.request
from decimal import Decimal
from unittest import mock

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "core"))
sys.path.insert(0, ROOT)

from core.adapters import engine_higgsfield_marketing as hm  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "higgsfield_marketing")
CATALOGUE = os.path.join(FIX, "catalogue.fixture.json")
HEADLINE = "0f6c2a7e-1d3b-4c55-9a10-5b7e8c9d0a11"
KIT = "9e8d7c6b-5a49-4382-9170-6f5e4d3c2b1a"


def fx(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return json.load(fh)


class NetworkUsed(AssertionError):
    pass


def _blocked(*_a, **_k):
    raise NetworkUsed("network/subprocess use is forbidden in adapter tests")


class MockTransport:
    """Replays recorded-shaped fixtures; records every call it receives."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def request(self, operation, params, credential):
        self.calls.append((operation, copy.deepcopy(params), credential))
        key = operation
        if operation == "dtc_ads.generate":
            key = f"dtc_ads.{'cost' if params.get('cost_only') else 'generate'}.{params['quality']}"
        if key not in self.responses:
            raise AssertionError(f"no fixture for {key}")
        return copy.deepcopy(self.responses[key])


def default_transport():
    return MockTransport({
        "brand_kits.upsert": fx("brand_kits.upsert.response.json"),
        "ad_formats.list": fx("ad_formats.list.response.json"),
        "dtc_ads.cost.low": fx("dtc_ads.cost.low.response.json"),
        "dtc_ads.cost.high": fx("dtc_ads.cost.high.response.json"),
        "dtc_ads.generate.low": fx("dtc_ads.generate.low.response.json"),
        "dtc_ads.generate.high": fx("dtc_ads.generate.high.response.json"),
    })


def credential(**kw):
    base = dict(workspace_id="b0c1d2e3-f405-4617-8829-3a4b5c6d7e8f",
                credential_id="c1d2e3f4-0516-4728-9930-4b5c6d7e8f90",
                secret_ref="vault:cs/ws-b0c1/higgsfield")
    base.update(kw)
    return hm.WorkspaceCredential(**base)


class GuardedTestCase(unittest.TestCase):
    def setUp(self):
        self._patches = [
            mock.patch.object(socket.socket, "connect", _blocked),
            mock.patch.object(socket.socket, "connect_ex", _blocked),
            mock.patch.object(socket, "create_connection", _blocked),
            mock.patch.object(socket, "getaddrinfo", _blocked),
            mock.patch.object(urllib.request, "urlopen", _blocked),
            mock.patch.object(urllib.request, "urlretrieve", _blocked),
            mock.patch.object(subprocess, "run", _blocked),
            mock.patch.object(subprocess, "Popen", _blocked),
            mock.patch.object(subprocess, "check_output", _blocked),
            mock.patch.object(os, "system", _blocked),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])
        self.cat = hm.load_catalogue(CATALOGUE)

    def assertRefusal(self, code, fn, *a, **k):
        with self.assertRaises(hm.ProviderRefusal) as ctx:
            fn(*a, **k)
        self.assertEqual(ctx.exception.code, code, str(ctx.exception))
        return ctx.exception


class TestGuard(GuardedTestCase):
    def test_guard_actually_blocks_network_and_subprocess(self):
        with self.assertRaises(NetworkUsed):
            socket.create_connection(("example.com", 443))
        with self.assertRaises(NetworkUsed):
            subprocess.run(["higgsfield", "--help"])

    def test_module_never_imports_subprocess_or_http_clients(self):
        src = open(hm.__file__, encoding="utf-8").read()
        for forbidden in ("import subprocess", "urllib", "requests", "http.client", "socket"):
            self.assertNotIn(forbidden, src)


class TestCatalogue(GuardedTestCase):
    def test_loads_and_maps_quality_to_tiers(self):
        self.assertEqual(self.cat.tier_for("low"), "draft")
        self.assertEqual(self.cat.tier_for("medium"), "draft")
        self.assertEqual(self.cat.tier_for("high"), "final")
        self.assertEqual(self.cat.references_max, 14)
        self.assertRegex(self.cat.sha256, r"^[0-9a-f]{64}$")

    def test_missing_catalogue_is_typed_refusal(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertRefusal("provider_catalogue_missing", hm.load_catalogue, None)
        self.assertRefusal("provider_catalogue_missing", hm.load_catalogue, "/nonexistent/catalogue.json")

    def test_env_var_path(self):
        with mock.patch.dict(os.environ, {hm.CATALOGUE_ENV: CATALOGUE}):
            self.assertEqual(hm.load_catalogue().sha256, self.cat.sha256)

    def test_invalid_catalogue_refused(self):
        raw = fx("catalogue.fixture.json")
        raw["quality_tiers"] = {"low": "draft"}
        self.assertRefusal("provider_catalogue_invalid", hm.parse_catalogue, raw)
        raw = fx("catalogue.fixture.json")
        del raw["limits"]["source"]["references_max"]
        self.assertRefusal("provider_catalogue_invalid", hm.parse_catalogue, raw)

    def test_ad_formats_come_from_catalogue_without_transport(self):
        fmts = hm.ad_formats(self.cat)
        self.assertEqual({f["id"] for f in fmts}, set(self.cat.formats))
        self.assertEqual([f["name"] for f in hm.ad_formats(self.cat, type="headline")], ["Headline"])

    def test_adapter_source_has_no_model_or_format_literals(self):
        src = open(hm.__file__, encoding="utf-8").read()
        for f in self.cat.formats:
            self.assertNotIn(f, src)
        for literal in ("nano_banana", "gpt_image", "seedream", "flux_", "z_image", "dtc-ads --quality"):
            self.assertNotIn(literal, src)


class TestCredentials(GuardedTestCase):
    def test_no_credential_is_typed_refusal_everywhere(self):
        tx = default_transport()
        e = self.assertRefusal("provider_credentials_missing", hm.brand_kit_upsert, fx("brand_kit_payload.json"), transport=tx)
        self.assertEqual(e.to_dict()["provider"], "higgsfield_marketing")
        self.assertRefusal("provider_credentials_missing", hm.cost_estimate, HEADLINE, "p", quality="low",
                           transport=tx, catalogue=self.cat)
        self.assertRefusal("provider_credentials_missing", hm.dtc_ad_generate, HEADLINE, KIT, None, [], "low",
                           prompt="p", transport=tx, catalogue=self.cat)
        self.assertEqual(tx.calls, [])

    def test_cli_session_mode_is_refused(self):
        self.assertRefusal("provider_credentials_missing", hm.dtc_ad_generate, HEADLINE, KIT, None, [], "low",
                           prompt="p", credential=credential(mode="cli_session"), transport=default_transport(),
                           catalogue=self.cat)

    def test_legacy_engine_seam_refuses_rather_than_shelling_out(self):
        self.assertRefusal("provider_credentials_missing", hm.generate, "p", None, {}, {})
        self.assertRefusal("provider_credentials_missing", hm.cost, "p", {}, {})

    def test_no_transport_is_typed_refusal(self):
        self.assertRefusal("provider_transport_unavailable", hm.dtc_ad_generate, HEADLINE, KIT, None, [], "low",
                           prompt="p", credential=credential(), catalogue=self.cat)


class TestBrandKit(GuardedTestCase):
    def test_upsert_pushes_our_payload_and_is_idempotent_on_hash(self):
        tx = default_transport()
        a = hm.brand_kit_upsert(fx("brand_kit_payload.json"), credential=credential(), transport=tx)
        b = hm.brand_kit_upsert(fx("brand_kit_payload.json"), credential=credential(), transport=tx)
        self.assertEqual(a["brand_kit_id"], KIT)
        self.assertEqual(a["payload_sha256"], b["payload_sha256"])
        op, params, _ = tx.calls[0]
        self.assertEqual(op, "brand_kits.upsert")
        self.assertEqual(params["idempotency_key"], a["payload_sha256"])
        self.assertEqual(params["brand_kit"]["name"], "Example Home Services Co")
        self.assertEqual(params["brand_kit"]["colours"][0]["hex"], "#1F4E79")

    def test_url_fetch_is_never_the_source_of_truth(self):
        self.assertRefusal("brand_kit_url_source_forbidden", hm.brand_kit_upsert,
                           {"url": "https://example-home.test"}, credential=credential(), transport=default_transport())
        payload = fx("brand_kit_payload.json")
        payload["source_url"] = "https://example-home.test"
        self.assertRefusal("brand_kit_url_source_forbidden", hm.brand_kit_upsert, payload,
                           credential=credential(), transport=default_transport())

    def test_invalid_payload_fields(self):
        for mutate in (lambda p: p.pop("fonts"), lambda p: p.__setitem__("colours", [{"hex": "blue", "role": "x"}]),
                       lambda p: p.__setitem__("logo_url", "http://insecure.test/logo.png"),
                       lambda p: p.__setitem__("values", "not-a-list")):
            payload = fx("brand_kit_payload.json")
            mutate(payload)
            self.assertRefusal("brand_kit_payload_invalid", hm.brand_kit_upsert, payload,
                               credential=credential(), transport=default_transport())

    def test_response_without_id_refused(self):
        tx = MockTransport({"brand_kits.upsert": {"brand_kit": {"name": "x"}}})
        self.assertRefusal("provider_response_invalid", hm.brand_kit_upsert, fx("brand_kit_payload.json"),
                           credential=credential(), transport=tx)


class TestGenerateAndCost(GuardedTestCase):
    def test_draft_tier_generate(self):
        tx = default_transport()
        out = hm.dtc_ad_generate(HEADLINE, KIT, "prod-01", ["media-a", {"id": "media-b", "role": "product"}], "low",
                                 prompt="compiled prompt", aspect_ratio="4:5", resolution="1k",
                                 credential=credential(), transport=tx, catalogue=self.cat)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["tier"], "draft")
        self.assertTrue(out[0]["text_model_drawn"])
        self.assertEqual(out[0]["result_url"], "https://cdn.provider.test/jobs/5a0e1b2c/result-0.png")
        op, params, cred = tx.calls[0]
        self.assertEqual(op, "dtc_ads.generate")
        self.assertEqual(params["media"], [{"id": "media-a", "role": "image"}, {"id": "media-b", "role": "product"}])
        self.assertEqual(params["product"], ["prod-01"])
        self.assertEqual(params["format_id"], HEADLINE)
        self.assertTrue(params["wait"])
        self.assertEqual(cred.credential_id, credential().credential_id)

    def test_final_tier_uses_reference_media(self):
        out = hm.dtc_ad_generate(HEADLINE, KIT, None, ["approved-draft-media"], "high", prompt="compiled prompt",
                                 credential=credential(), transport=default_transport(), catalogue=self.cat)
        self.assertEqual(out[0]["tier"], "final")
        self.assertEqual(out[0]["reference_media_ids"], ["approved-draft-media"])

    def test_reference_limit_from_catalogue(self):
        self.assertRefusal("references_exceed_limit", hm.dtc_ad_generate, HEADLINE, KIT, None,
                           [f"m{i}" for i in range(15)], "low", prompt="p", credential=credential(),
                           transport=default_transport(), catalogue=self.cat)
        out = hm.dtc_ad_generate(HEADLINE, KIT, None, [f"m{i}" for i in range(14)], "low", prompt="p",
                                 credential=credential(), transport=default_transport(), catalogue=self.cat)
        self.assertEqual(len(out[0]["reference_media_ids"]), 14)

    def test_local_paths_and_urls_are_not_uploaded_here(self):
        self.assertRefusal("reference_upload_required", hm.dtc_ad_generate, HEADLINE, KIT, None,
                           ["https://cdn.example.test/a.png"], "low", prompt="p", credential=credential(),
                           transport=default_transport(), catalogue=self.cat)
        self.assertRefusal("reference_upload_required", hm.dtc_ad_generate, HEADLINE, KIT, None,
                           ["/tmp/a.png"], "low", prompt="p", credential=credential(),
                           transport=default_transport(), catalogue=self.cat)

    def test_unknown_format_quality_and_canvas(self):
        args = dict(prompt="p", credential=credential(), transport=default_transport(), catalogue=self.cat)
        self.assertRefusal("ad_format_unknown", hm.dtc_ad_generate, "00000000-0000-4000-8000-000000000000", KIT, None, [], "low", **args)
        self.assertRefusal("quality_unknown", hm.dtc_ad_generate, HEADLINE, KIT, None, [], "ultra", **args)
        self.assertRefusal("aspect_ratio_unsupported", hm.dtc_ad_generate, HEADLINE, KIT, None, [], "low", aspect_ratio="21:9", **args)

    def test_failed_job_is_typed_not_swallowed(self):
        tx = MockTransport({"dtc_ads.generate.low": fx("dtc_ads.generate.failed.response.json")})
        self.assertRefusal("generation_not_completed", hm.dtc_ad_generate, HEADLINE, KIT, None, [], "low",
                           prompt="p", credential=credential(), transport=tx, catalogue=self.cat)

    def test_cost_estimate_without_sourced_price_reports_missing(self):
        est = hm.cost_estimate(HEADLINE, "p", tier="draft", brand_kit_id=KIT, credential=credential(),
                               transport=default_transport(), catalogue=self.cat)
        self.assertEqual(est["credits"], "1")
        self.assertEqual(est["quality"], "low")
        self.assertIsNone(est["usd"])
        self.assertEqual(est["usd_status"], "missing")
        self.assertIn("credit_price_unknown", est["missing_reason"])

    def test_cost_estimate_with_sourced_price(self):
        cred = credential(credit_unit_price_usd=Decimal("0.0625"), credit_price_source="workspace contract #fixture")
        tx = default_transport()
        est = hm.cost_estimate(HEADLINE, "p", tier="final", credential=cred, transport=tx, catalogue=self.cat)
        self.assertEqual(est["tier"], "final")
        self.assertEqual(est["credits"], "6.5")
        self.assertEqual(est["usd"], "0.406250")
        self.assertTrue(tx.calls[0][1]["cost_only"])
        self.assertNotIn("wait", tx.calls[0][1])

    def test_price_without_source_is_not_used(self):
        cred = credential(credit_unit_price_usd=Decimal("0.0625"))
        est = hm.cost_estimate(HEADLINE, "p", tier="draft", credential=cred, transport=default_transport(), catalogue=self.cat)
        self.assertIsNone(est["usd"])


class TestExecutePlan(GuardedTestCase):
    def plan(self, **kw):
        p = {"schema_version": "provider-execution-plan-v1", "id": "plan-1", "provider": "higgsfield_marketing",
             "model": HEADLINE, "method": "generate",
             "params": {"format_id": HEADLINE, "brand_kit_id": KIT, "quality": "low"},
             "prompt": {"positive": "compiled positive prompt", "negative": None},
             "output": {"aspect_ratio": "9:16", "resolution": "2k", "count": 1},
             "media_inputs": [{"id": "ref-media-1", "role": "image"}]}
        p.update(kw)
        return p

    def test_plan_generate(self):
        tx = default_transport()
        out = hm.execute_plan(self.plan(), None, credential=credential(), transport=tx, catalogue=self.cat)
        self.assertEqual(out[0]["tier"], "draft")
        params = tx.calls[0][1]
        self.assertEqual(params["aspect_ratio"], "9:16")
        self.assertEqual(params["resolution"], "2k")
        self.assertEqual(params["prompt"], "compiled positive prompt")

    def test_plan_cost_estimate_and_dry_run(self):
        tx = default_transport()
        est = hm.execute_plan(self.plan(method="cost_estimate"), None, credential=credential(), transport=tx, catalogue=self.cat)
        self.assertEqual(est["credits"], "1")
        est2 = hm.execute_plan(self.plan(), None, dry_run=True, credential=credential(), transport=tx, catalogue=self.cat)
        self.assertEqual(est2["credits"], "1")
        self.assertTrue(all(c[1].get("cost_only") for c in tx.calls))

    def test_graph_runtime_call_shape_refuses_without_credential(self):
        # graph_runtime calls execute_plan(plan, refs, job_dir, dry_run=...) positionally.
        self.assertRefusal("provider_credentials_missing", hm.execute_plan, self.plan(), [], ".", False, catalogue=self.cat)

    def test_wrong_provider_refused(self):
        self.assertRefusal("plan_provider_mismatch", hm.execute_plan, self.plan(provider="higgsfield"), None,
                           credential=credential(), transport=default_transport(), catalogue=self.cat)


if __name__ == "__main__":
    unittest.main()
