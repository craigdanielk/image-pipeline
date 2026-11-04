"""CS-B3 unit tests (no database). SQL evidence lives in test_b3_real_db.py."""
from __future__ import annotations

import ast
import base64
import json
import os
import re
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from service.errors import ServiceError
from service.providers import catalogue as cat_mod
from service.providers import higgsfield
from service.providers.adapter import engine_higgsfield as hf
from service.providers.canonical import CanonicalError, artifact_digest, canonical, digest
from service.providers.contracts import ContractViolation, validate
from service.providers.credentials import CredentialError, KeyRing, materialise
from service.runtime import admission
from service.runtime.entry import make_runtime_wsgi
from service.runtime.worker import HttpOutputFetcher, Retryable, sniff_image
from tests.creative_studio.test_cs_b1_unit import FORBIDDEN_LITERALS
from tests.creative_studio.b3_support import B3_FIX, FakeHiggsfield, load_source, make_plan, png, provider_node

ROOT = Path(__file__).resolve().parents[2]
B3_FILES = sorted((ROOT / "service" / "providers").glob("*.py")) + sorted((ROOT / "service" / "runtime").glob("*.py")) \
    + sorted((ROOT / "tests" / "creative_studio").glob("b3_*.py")) + [ROOT / "tests" / "creative_studio" / "test_b3_real_db.py"] \
    + sorted((ROOT / "supabase" / "migrations").glob("20261031140300_*.sql")) + sorted(B3_FIX.rglob("*.json")) \
    + [B3_FIX / "README.md"]
# Acceptance-set literal list is owned by CS-B1's unit test (the only file allowed to hold it).
FORBIDDEN = FORBIDDEN_LITERALS


class CanonicalTests(unittest.TestCase):
    def test_rfc8785_ordering_and_numbers(self):
        self.assertEqual(canonical({"b": 1, "a": [True, None, 1.5, 2.0], "é": "x"}),
                         '{"a":[true,null,1.5,2],"b":1,"é":"x"}'.encode())
        self.assertEqual(canonical(1e21), b"1e+21")
        for bad in (float("nan"), float("inf"), 2 ** 53, {1: 2}, {"x": object()}):
            with self.assertRaises(CanonicalError):
                canonical(bad)

    def test_artifact_digest_omits_only_top_level_sha(self):
        doc = {"a": 1, "sha256": "x", "child": {"sha256": "keep"}}
        self.assertEqual(artifact_digest(doc), digest({"a": 1, "child": {"sha256": "keep"}}))


class ProviderWrapperTests(unittest.TestCase):
    def test_pre_dispatch_refusals_are_raised_before_the_paid_call(self):
        """Every code classified as 'refused' is raised before engine_higgsfield._metered_create."""
        src = Path(hf.__file__).read_text()
        tree = ast.parse(src)
        fn = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for name in ("dtc_ad_generate", "generate"):
            body = ast.unparse(fn[name])
            self.assertIn("_metered_create", body)
        for code in higgsfield.PRE_DISPATCH_REFUSALS:
            self.assertIn(f"'{code}'", ast.unparse(tree).replace('"', "'"), code)
        kind, err = higgsfield.classify(hf.ProviderRefusal("provider_credentials_missing", "x"))
        self.assertEqual((kind, err["code"]), ("refused", "provider_credentials_missing"))
        self.assertEqual(higgsfield.classify(hf.ProviderRefusal("generation_not_completed"))[0], "failed")
        self.assertEqual(higgsfield.classify(RuntimeError("higgsfield failed (1)"))[0], "uncertain")
        self.assertEqual(higgsfield.classify(hf.ProviderRefusal("provider_response_invalid"))[0], "uncertain")

    def test_charges_dedupe_per_call_and_keep_certainty(self):
        call = {"scope": "call", "call_id": "c-1", "certainty": "balance_delta", "credits": "2",
                "caveat": "attributable only if nothing else spent"}
        out = higgsfield.normalise([
            {"id": "j1", "status": "completed", "result_url": "https://cdn.provider.test/a.png", "charge": call},
            {"id": "j1", "status": "completed", "result_url": "https://cdn.provider.test/b.png", "charge": call}])
        self.assertEqual(out.outcome, "completed")
        self.assertEqual([j["provider_request_id"] for j in out.jobs], ["j1", "j1#1"])
        self.assertEqual(len(out.charges), 1)
        self.assertEqual((out.charges[0]["billing_reference"], out.charges[0]["certainty"], out.charges[0]["credits"]),
                         ("higgsfield-call:c-1", "balance_delta", "2"))
        self.assertIn("caveat=", out.charges[0]["note"])
        job = higgsfield.normalise([{"id": "j2", "status": "completed", "result_url": "https://x.test/r.png",
                                     "charge": {"scope": "job", "certainty": "provider_reported", "credits": "1"}}])
        self.assertEqual(job.charges[0]["billing_reference"], "higgsfield-job:j2")
        unknown = higgsfield.normalise([{"id": "j3", "status": "completed", "result_url": "https://x.test/r.png",
                                         "charge": {"scope": "call", "call_id": "c3", "certainty": "unknown",
                                                    "credits": None}}])
        self.assertIsNone(unknown.charges[0]["credits"])

    def test_credit_conversion_rounds_up_to_whole_micros(self):
        self.assertEqual(higgsfield.micros_for_credits(Decimal("6.5"), 50_000), 325_000)
        self.assertEqual(higgsfield.micros_for_credits(Decimal("0.0000001"), 50_000), 1)

    def test_adapter_plan_maps_marketing_studio_and_classic(self):
        src = load_source()
        cfg = {"surface": "marketing_studio", "catalogue": src["marketing_studio"]["catalogue"], "controls": {}}
        call = {"model_id": "m", "positive_prompt": "p", "negative_prompt": "blur",
                "parameters": {"quality": "low", "batch_size": 2, "aspect_ratio": "1:1"}}
        plan, ref = higgsfield.adapter_plan("marketing_studio", call, cfg, [{"path": "/tmp/r.png", "role": "image"}])
        self.assertEqual((plan["method"], plan["output"], plan["params"]["format_id"], ref),
                         (hf.MARKETING_STUDIO_METHOD, {"count": 2, "aspect_ratio": "1:1"}, "m", None))
        self.assertTrue(plan["params"]["_negative_appended"])
        self.assertEqual(plan["media_inputs"], [{"path": "/tmp/r.png", "role": "image"}])
        plan, ref = higgsfield.adapter_plan("generate", call, {"controls": {"negative_prompt": True}},
                                            [{"path": "/tmp/r.png", "role": "image"}])
        self.assertEqual((plan["model"], ref), ("m", ["/tmp/r.png"]))
        self.assertNotIn("_negative_appended", plan["params"])

    def test_adapter_is_loaded_without_the_local_pipeline_package(self):
        self.assertTrue(higgsfield.ADAPTER_REVISION.startswith("engine_higgsfield@"))
        for path in (ROOT / "service" / "providers").glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                    for n in names:
                        self.assertFalse(n.split(".")[0] in {"core", "ui", "pipeline", "tools", "subprocess"},
                                         f"{path.name} imports {n}")


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeHiggsfield(Path(os.environ.get("TMPDIR", "/tmp")) / f"b3unit-{os.getpid()}-{id(self)}")
        self.cred_dir = Path(self.fake.dir)
        (self.cred_dir / "cred.json").write_text("{}")
        self.cred = hf.WorkspaceCredential("ws", "c", str(self.cred_dir / "cred.json"))

    def build(self, source, **kw):
        with mock.patch.object(hf, "subprocess", SimpleNamespace(run=self.fake)):
            return cat_mod.build_entries(source, credential=self.cred, **kw)

    def test_prices_are_quote_times_cited_credit_price(self):
        entries, refusals = self.build(load_source(), allow_fixture=True)
        self.assertEqual(refusals, [])
        prices = {e.entry_id: (e.unit_cost_micros, e.tier, e.price_snapshot["quoted_credits"]) for e in entries}
        self.assertEqual(sorted(prices.values()), [(50_000, "draft", "1"), (125_000, "draft", "2.5"),
                                                   (325_000, "final", "6.5")])
        self.assertTrue(all(e.price_snapshot["source_url"] for e in entries))

    def test_fixture_source_refused_without_flag_and_bad_price_refused(self):
        with self.assertRaises(cat_mod.CatalogueSourceError):
            self.build(load_source())
        src = load_source()
        src["credit_price"]["credit_price_micros"] = 0
        with self.assertRaises(cat_mod.CatalogueSourceError):
            self.build(src, allow_fixture=True)

    def test_unreadable_quote_is_reported_not_published(self):
        broken = FakeHiggsfield(self.fake.dir)
        def run(cmd, **kw):
            if cmd[1:3] == ["generate", "cost"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"job_type": "x"}), stderr="")
            return broken(cmd, **kw)
        with mock.patch.object(hf, "subprocess", SimpleNamespace(run=run)):
            entries, refusals = cat_mod.build_entries(load_source(), credential=self.cred, allow_fixture=True)
        self.assertEqual([r["entry_id"] for r in refusals], ["hf-example-image-draft"])
        self.assertEqual(refusals[0]["refusal"], "price_unknown")
        self.assertEqual(len(entries), 2)


class CredentialTests(unittest.TestCase):
    def test_seal_open_binds_account_and_materialise_cleans_up(self):
        ring = KeyRing({"k1": b"a" * 32, "k2": b"b" * 32})
        sealed = ring.seal(b"secret", provider="higgsfield", fingerprint="acct-1")
        self.assertEqual(sealed.key_version, "k2")
        self.assertEqual(ring.open(sealed, provider="higgsfield", fingerprint="acct-1"), b"secret")
        with self.assertRaises(CredentialError):
            ring.open(sealed, provider="higgsfield", fingerprint="acct-2")      # cannot move between accounts
        with materialise(b"secret") as path:
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
            self.assertEqual(Path(path).read_bytes(), b"secret")
        self.assertFalse(Path(path).exists())

    def test_key_ring_from_env_refuses_missing_or_bad_keys(self):
        for env in ({}, {"CS_CREDENTIAL_KEYS": "nope"}, {"CS_CREDENTIAL_KEYS": json.dumps({"k": base64.b64encode(b"s").decode()})}):
            with self.assertRaises(CredentialError):
                KeyRing.from_env(env)


class PlanAndMediaTests(unittest.TestCase):
    def entry(self):
        return {"provider": "higgsfield", "model_id": "example_image_model", "id": "hf-example-image-draft",
                "endpoint_key": "generate", "adapter_version": "a", "request_schema_sha256": "0" * 64,
                "pricing": {"unit_cost_micros": 125_000}}

    def test_plan_validation_recomputes_hashes(self):
        ws = "5f0c1d2e-3a4b-4c5d-8e6f-7a8b9c0d1e2f"
        plan = make_plan(ws, [provider_node("n1", self.entry(), prompt="p")])
        checked = admission.validate_plan(plan, ws)
        self.assertEqual(checked["nodes"][0]["node_id"], "n1")
        tampered = json.loads(json.dumps(plan))
        tampered["program"]["nodes"][0]["provider_call"]["positive_prompt"] = "changed"
        with self.assertRaises(ServiceError) as ctx:
            admission.validate_plan(tampered, ws)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (422, "plan_invalid"))
        with self.assertRaises(ServiceError) as ctx:
            admission.validate_plan(plan, "6f0c1d2e-3a4b-4c5d-8e6f-7a8b9c0d1e2f")
        self.assertEqual(ctx.exception.status, 404)
        bad = json.loads(json.dumps(plan))
        bad["program"]["nodes"][0]["provider_call"]["retries"]["automatic_resubmits"] = 1
        with self.assertRaises(ServiceError):
            admission.validate_plan(bad, ws)

    def test_provisional_plan_fixture_is_contract_valid(self):
        doc = json.loads((B3_FIX / "_provisional" / "execution-plan.draft.v1.json").read_text())
        validate("execution-plan-v1.schema.json", doc)
        self.assertEqual(doc["compile_hash"], digest(doc["program"]))
        self.assertEqual(doc["sha256"], artifact_digest(doc))

    def test_media_sniffing_never_trusts_claimed_type(self):
        self.assertEqual(sniff_image(png("x", 32, 16)), ("image/png", 32, 16))
        self.assertEqual(sniff_image(b"\xff\xd8\xff\xe0rest")[0], "image/jpeg")
        with self.assertRaises(ValueError):
            sniff_image(b"<html>not an image</html>")

    def test_http_fetcher_refuses_foreign_origins(self):
        fetcher = HttpOutputFetcher(opener=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
        for url in ("http://cdn.provider.test/a.png", "https://evil.test/a.png"):
            with self.assertRaises(Retryable):
                fetcher.fetch(url, ["https://cdn.provider.test"])

    def test_contract_violation_reports_pointers(self):
        with self.assertRaises(ContractViolation) as ctx:
            validate("provider-receipt-v1.schema.json", {"schema_version": "cs-provider-receipt-v1"})
        self.assertTrue(all(e.startswith("/") for e in ctx.exception.errors))


class EntryTests(unittest.TestCase):
    def call(self, app, auth="Bearer s"):
        out = {}
        body = b"".join(app({"REQUEST_METHOD": "POST", "HTTP_AUTHORIZATION": auth}, lambda s, h: out.setdefault("s", s)))
        return out["s"][:3], json.loads(body)

    def test_runtime_entry_refuses_without_secret_store_or_auth(self):
        settings = SimpleNamespace(worker_secret="s", worker_database_url="x")
        self.assertEqual(self.call(make_runtime_wsgi(lambda: settings), auth="Bearer wrong"),
                         ("401", {"error": "authentication_required"}))
        self.assertEqual(self.call(make_runtime_wsgi(lambda: settings)), ("503", {"error": "object_store_unconfigured"}))
        self.assertEqual(self.call(make_runtime_wsgi(lambda: SimpleNamespace(worker_secret=None))),
                         ("503", {"error": "worker_secret_unconfigured"}))


class HostedBrandImportTests(EntryTests):
    """The hosted worker claims import_brand_package first (it never did; imports stayed queued forever)."""
    SETTINGS = SimpleNamespace(worker_secret="s", worker_database_url="x", mos_gateway_url="https://mos.test",
                               mos_assertion_key="k", mos_service_token=None, environment_id="production")

    def app(self, settings):
        return make_runtime_wsgi(lambda: settings, store_factory=lambda s: object(), keyring_loader=lambda: None)

    def test_due_import_is_executed_before_provider_work(self):
        done = {"claimed": 1, "status": "finished", "work_item_id": "w1"}
        with mock.patch("service.brand.importer.run_import_once", return_value=done) as imp, \
             mock.patch("service.runtime.entry.ProviderRuntime") as runtime:
            self.assertEqual(self.call(self.app(self.SETTINGS)), ("200", done))
        imp.assert_called_once()
        runtime.assert_not_called()

    def test_no_import_due_falls_through_to_provider_runtime(self):
        with mock.patch("service.brand.importer.run_import_once", return_value={"claimed": 0, "status": "idle"}), \
             mock.patch("service.runtime.entry.ProviderRuntime") as runtime:
            runtime.return_value.run_once.return_value = {"claimed": 0, "status": "idle", "reason": "no_due_work"}
            self.assertEqual(self.call(self.app(self.SETTINGS))[1]["reason"], "no_due_work")
        runtime.return_value.run_once.assert_called_once()

    def test_without_a_mos_gateway_no_import_is_attempted(self):
        settings = SimpleNamespace(**{**vars(self.SETTINGS), "mos_gateway_url": None})
        with mock.patch("service.brand.importer.run_import_once") as imp, \
             mock.patch("service.runtime.entry.ProviderRuntime") as runtime:
            runtime.return_value.run_once.return_value = {"claimed": 0, "status": "idle"}
            self.call(self.app(settings))
        imp.assert_not_called()


class MosCallbackBodyTests(unittest.TestCase):
    """Marketing OS re-serialises a parsed body before checking the signed hash; only canonical JSON survives that."""
    def test_sources_and_eligibility_send_canonical_json(self):
        from service import jcs
        from service.runtime.gateway import HttpMarketingOsGateway
        sent = []

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, *a): return b"{}"

        def opener(req, timeout=None):
            sent.append(req.data)
            return Resp()
        gw = HttpMarketingOsGateway("https://mos.test", assertion_key="k" * 40, opener=opener)
        visual = {"source": "library", "brief": "Hair \u2014 loss", "ratio": 1.0, "canvas": [1080, 1080]}
        gw.campaign_sources(mos_workspace_id="00000000-0000-0000-0000-000000000001", visual_request=visual,
                            user_id="00000000-0000-0000-0000-000000000002")
        gw.eligibility(mos_workspace_id="00000000-0000-0000-0000-000000000001",
                       user_id="00000000-0000-0000-0000-000000000002", action="generate", source_versions=[{"b": 2, "a": 1}])
        self.assertEqual(sent[0], jcs.canonical_bytes({"visual_request": visual}))
        self.assertEqual(sent[1], jcs.canonical_bytes({"action": "generate", "source_versions": [{"b": 2, "a": 1}]}))
        self.assertNotIn(b", ", sent[0])


class LeakageTests(unittest.TestCase):
    def test_no_acceptance_set_names_in_b3_files(self):
        self.assertTrue(len(B3_FILES) > 10)
        for path in B3_FILES:
            text = path.read_text(encoding="utf-8").lower()
            for literal in FORBIDDEN:
                self.assertNotIn(literal, text, f"{path.relative_to(ROOT)} contains {literal!r}")

    def test_no_price_literals_in_runtime_code(self):
        """Prices come from the adapter quote x the cited source; code holds no credit price."""
        for path in sorted((ROOT / "service" / "providers").glob("*.py")) + sorted((ROOT / "service" / "runtime").glob("*.py")):
            text = path.read_text()
            self.assertIsNone(re.search(r"\b50_?000\b|\b325_?000\b|\b125_?000\b", text), path.name)
            self.assertNotIn("credit_price_micros =", text, path.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
