#!/usr/bin/env python3
"""Tests for core/adapters/engine_higgsfield.py (the single Higgsfield adapter).

Zero network, zero real processes: socket/DNS/urllib/Popen/os.system raise, and
`subprocess.run` is replaced by FakeCLI, which answers `higgsfield …` argv from
recorded-shaped fixtures in tests/fixtures/higgsfield/ and records argv + env.
"""
from __future__ import annotations

import copy
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from contextlib import redirect_stdout
from unittest import mock

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "core"))
sys.path.insert(0, ROOT)

from core.adapters import engine_higgsfield as hf  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "higgsfield")
CATALOGUE = os.path.join(FIX, "catalogue.fixture.json")
HEADLINE = "0f6c2a7e-1d3b-4c55-9a10-5b7e8c9d0a11"
KIT = "9e8d7c6b-5a49-4382-9170-6f5e4d3c2b1a"
MEDIA_A = "c1a2b3c4-d5e6-47f8-8a9b-0c1d2e3f4a5b"
MEDIA_B = "d2b3c4d5-e6f7-4809-9bac-1d2e3f4a5b6c"


def fx(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return json.load(fh)


class NetworkUsed(AssertionError):
    pass


def _blocked(*_a, **_k):
    raise NetworkUsed("network / real process use is forbidden in adapter tests")


class FakeCLI:
    """Stands in for subprocess.run. Routes `higgsfield …` argv to fixtures."""

    def __init__(self, **routes):
        self.calls = []  # (argv[1:], env)
        self.balances = list(routes.pop("balances", [fx("account.status.before.json"), fx("account.status.after_draft.json")]))
        self.routes = {
            "dtc.generate.low": fx("dtc_ads.generate.low.response.json"),
            "dtc.generate.high": fx("dtc_ads.generate.high.response.json"),
            "dtc.cost.low": fx("dtc_ads.cost.low.response.json"),
            "dtc.cost.high": fx("dtc_ads.cost.high.response.json"),
            "generate.create": fx("generate.create.response.json"),
            "generate.cost": fx("generate.cost.response.json"),
            "upload.create": fx("upload.create.response.json"),
        }
        self.routes.update(routes)

    def ops(self):
        return [self.key(a) for a, _ in self.calls]

    @staticmethod
    def key(a):
        if a[:3] == ["marketing-studio", "dtc-ads", "generate"]:
            q = a[a.index("--quality") + 1]
            return f"dtc.{'cost' if '--cost-only' in a else 'generate'}.{q}"
        return ".".join(a[:2])

    def __call__(self, cmd, capture_output=False, text=False, env=None, **_kw):
        if not isinstance(cmd, list) or cmd[0] != "higgsfield":
            raise NetworkUsed(f"unexpected process: {cmd!r}")
        args = cmd[1:]
        self.calls.append((args, env))
        k = self.key(args)
        if k == "account.status":
            if not self.balances:
                return subprocess.CompletedProcess(cmd, 1, "", "Not authenticated.")
            out = self.balances.pop(0)
        else:
            out = self.routes.get(k)
        if isinstance(out, Exception):
            return subprocess.CompletedProcess(cmd, 1, "", str(out))
        if out is None:
            raise AssertionError(f"no fixture route for {k}: {args}")
        stdout = out if isinstance(out, str) else json.dumps(copy.deepcopy(out))
        return subprocess.CompletedProcess(cmd, 0, stdout, "")


class Base(unittest.TestCase):
    def setUp(self):
        self.cli = FakeCLI()
        patches = [
            mock.patch.object(socket.socket, "connect", _blocked),
            mock.patch.object(socket.socket, "connect_ex", _blocked),
            mock.patch.object(socket, "create_connection", _blocked),
            mock.patch.object(socket, "getaddrinfo", _blocked),
            mock.patch.object(urllib.request, "urlopen", _blocked),
            mock.patch.object(urllib.request, "urlretrieve", _blocked),
            mock.patch.object(subprocess, "Popen", _blocked),
            mock.patch.object(subprocess, "check_output", _blocked),
            mock.patch.object(os, "system", _blocked),
            mock.patch.object(subprocess, "run", self._run),
            mock.patch.dict(os.environ, {}, clear=False),
            redirect_stdout(io.StringIO()),
        ]
        for p in patches:
            p.__enter__()
            self.addCleanup(p.__exit__, None, None, None)
        for k in (hf.DEV_LOGIN_ENV, hf.HOSTED_RUNTIME_ENV, "HIGGSFIELD_WORKSPACE_ID", "HIGGSFIELD_CREDENTIALS_PATH"):
            os.environ.pop(k, None)
        self.cat = hf.load_catalogue(CATALOGUE)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cred_file = os.path.join(self.tmp.name, "ws-credentials.json")
        with open(self.cred_file, "w") as fh:
            fh.write("{}")

    def _run(self, *a, **k):
        return self.cli(*a, **k)

    def credential(self, **kw):
        base = dict(workspace_id="b0c1d2e3-f405-4617-8829-3a4b5c6d7e8f",
                    credential_id="c1d2e3f4-0516-4728-9930-4b5c6d7e8f90",
                    credentials_path=self.cred_file, provider_workspace_id="hf-ws-example")
        base.update(kw)
        return hf.WorkspaceCredential(**base)

    def refused(self, code, fn, *a, **k):
        with self.assertRaises(hf.ProviderRefusal) as ctx:
            fn(*a, **k)
        self.assertEqual(ctx.exception.code, code, str(ctx.exception))
        return ctx.exception

    def dtc(self, quality="low", **kw):
        args = dict(prompt="compiled prompt", credential=self.credential(), catalogue=self.cat)
        args.update(kw)
        refs = args.pop("references", [])
        return hf.dtc_ad_generate(HEADLINE, KIT, None, refs, quality, **args)


class TestGuardAndShape(Base):
    def test_guard_blocks_network_and_real_processes(self):
        with self.assertRaises(NetworkUsed):
            socket.create_connection(("example.com", 443))
        with self.assertRaises(NetworkUsed):
            subprocess.Popen(["higgsfield"])
        with self.assertRaises(NetworkUsed):
            subprocess.run(["sh", "-c", "true"])

    def test_single_adapter(self):
        adapters_dir = os.path.dirname(hf.__file__)
        self.assertFalse(os.path.exists(os.path.join(adapters_dir, "engine_higgsfield_marketing.py")))
        from core.adapters import get_engine
        self.assertIs(get_engine("higgsfield"), hf)
        src = open(hf.__file__, encoding="utf-8").read()
        for gone in ("class Transport", "require_transport", "credit_unit_price_usd", "def _price"):
            self.assertNotIn(gone, src)

    def test_no_format_ids_or_marketing_model_literals_in_adapter(self):
        src = open(hf.__file__, encoding="utf-8").read()
        for f in self.cat.formats:
            self.assertNotIn(f, src)


class TestCredentials(Base):
    def test_no_credential_is_typed_refusal_and_runs_nothing(self):
        self.refused("provider_credentials_missing", hf.cost, "p", {}, {"model": "example_image_model"})
        self.refused("provider_credentials_missing", hf.generate, "p", None, {}, {"model": "example_image_model"})
        self.refused("provider_credentials_missing", hf.dtc_ad_generate, HEADLINE, KIT, None, [], "low",
                     prompt="p", catalogue=self.cat)
        self.refused("provider_credentials_missing", hf.dtc_ad_cost, HEADLINE, "p", quality="low", catalogue=self.cat)
        self.assertEqual(self.cli.calls, [])

    def test_refusal_is_a_runtime_error_for_existing_callers(self):
        with self.assertRaises(RuntimeError):
            hf.cost("p", {}, {"model": "example_image_model"})

    def test_graph_runtime_call_shape_refuses_without_dev_login(self):
        plan = {"provider": "higgsfield", "model": "example_image_model", "method": "generate",
                "prompt": {"positive": "p"}, "output": {}}
        self.refused("provider_credentials_missing", hf.execute_plan, plan, [], ".", True)

    def test_dev_cli_login_only_with_flag_and_never_hosted(self):
        os.environ[hf.DEV_LOGIN_ENV] = "1"
        hf.cost("p", {}, {"model": "example_image_model"})
        self.assertIsNone(self.cli.calls[-1][1], "dev path inherits the machine login env")
        os.environ[hf.HOSTED_RUNTIME_ENV] = "1"
        self.refused("provider_credentials_missing", hf.cost, "p", {}, {"model": "example_image_model"})

    def test_workspace_credential_reaches_cli_via_env_only(self):
        os.environ["HIGGSFIELD_WORKSPACE_ID"] = "machine-default-ws"
        hf.cost("secret prompt", {}, {"model": "example_image_model"}, credential=self.credential())
        args, env = self.cli.calls[-1]
        self.assertEqual(env["HIGGSFIELD_CREDENTIALS_PATH"], self.cred_file)
        self.assertEqual(env["HIGGSFIELD_WORKSPACE_ID"], "hf-ws-example")
        self.assertNotIn(self.cred_file, " ".join(args))
        hf.cost("p", {}, {"model": "example_image_model"}, credential=self.credential(provider_workspace_id=None))
        self.assertNotIn("HIGGSFIELD_WORKSPACE_ID", self.cli.calls[-1][1])

    def test_bad_credentials_refused(self):
        for cred in (self.credential(mode="cli_session"), self.credential(credentials_path="relative.json"),
                     self.credential(credentials_path=os.path.join(self.tmp.name, "absent.json")),
                     self.credential(credential_id="")):
            self.refused("provider_credentials_missing", hf.cost, "p", {}, {"model": "m"}, credential=cred)
        self.assertEqual(self.cli.calls, [])

    def test_prompt_is_redacted_in_logs(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            hf.cost("very secret client brief", {}, {"model": "example_image_model"}, credential=self.credential())
        self.assertNotIn("very secret", buf.getvalue())
        self.assertIn("<24 chars>", buf.getvalue())


class TestClassicCharge(Base):
    def test_charge_from_job_json(self):
        self.cli.routes["generate.create"] = fx("generate.create.charged.response.json")
        out = hf.generate("p", None, {}, {"model": "example_image_model"}, credential=self.credential())
        self.assertEqual(out[0]["charge"]["certainty"], "provider_reported")
        self.assertEqual(out[0]["charge"]["credits"], "2.5")
        self.assertEqual(out[0]["charge"]["scope"], "job")
        self.assertNotIn("generate.cost", self.cli.ops())

    def test_charge_from_balance_delta(self):
        out = hf.generate("p", None, {}, {"model": "example_image_model"}, credential=self.credential())
        ch = out[0]["charge"]
        self.assertEqual((ch["certainty"], ch["credits"], ch["balance_before"], ch["balance_after"]),
                         ("balance_delta", "1", "500", "499"))
        self.assertEqual(ch["job_ids"], [out[0]["id"]])
        self.assertEqual(self.cli.ops(), ["account.status", "generate.create", "account.status"])

    def test_charge_falls_back_to_estimate_explicitly(self):
        self.cli.balances = []  # account status unreadable
        out = hf.generate("p", None, {}, {"model": "example_image_model"}, credential=self.credential())
        ch = out[0]["charge"]
        self.assertEqual((ch["certainty"], ch["credits"], ch["source"]), ("estimated", "2.5", "generate_cost_estimate"))
        self.assertIn("balance_unreadable", ch["fallback_reason"])

    def test_balance_rise_is_not_counted_as_a_charge(self):
        self.cli.balances = [fx("account.status.before.json"), fx("account.status.topped_up.json")]
        ch = hf.generate("p", None, {}, {"model": "example_image_model"}, credential=self.credential())[0]["charge"]
        self.assertEqual(ch["certainty"], "estimated")
        self.assertIn("balance_delta_not_attributable", ch["fallback_reason"])

    def test_unknown_when_nothing_is_readable_but_results_are_kept(self):
        self.cli.balances = []
        self.cli.routes["generate.cost"] = RuntimeError("cost endpoint down")
        out = hf.generate("p", None, {}, {"model": "example_image_model"}, credential=self.credential())
        self.assertEqual(out[0]["result_url"], "https://cdn.provider.test/jobs/9e4c5f60/result-0.png")
        self.assertEqual(out[0]["charge"]["certainty"], "unknown")
        self.assertIsNone(out[0]["charge"]["credits"])
        self.assertIn("cost endpoint down", out[0]["charge"]["estimate_error"])

    def test_non_json_is_an_error(self):
        self.cli.routes["generate.cost"] = "Credits: 2.5"
        with self.assertRaises(RuntimeError) as ctx:
            hf.cost("p", {}, {"model": "example_image_model"}, credential=self.credential())
        self.assertIn("non-JSON", str(ctx.exception))

    def test_image_references_flag(self):
        ref = os.path.join(self.tmp.name, "ref.png")
        open(ref, "wb").close()
        hf.generate("p", None, {}, {"model": "example_image_model", "params": {"input_images": [ref]}},
                    credential=self.credential())
        create = next(a for a, _ in self.cli.calls if a[:2] == ["generate", "create"])
        self.assertEqual(create[create.index("--image-references") + 1], ref)


class TestMarketingStudio(Base):
    def test_draft_generate_cli_shape_and_charge(self):
        out = self.dtc("low", references=[MEDIA_A, {"id": MEDIA_B, "role": "product"}], aspect_ratio="4:5",
                       resolution="1k")
        self.assertEqual(len(out), 1)
        r = out[0]
        self.assertEqual((r["provider"], r["surface"], r["tier"]), ("higgsfield", "marketing_studio", "draft"))
        self.assertTrue(r["text_model_drawn"])
        self.assertEqual(r["charge"]["certainty"], "balance_delta")
        args = next(a for a, _ in self.cli.calls if self.cli.key(a) == "dtc.generate.low")
        self.assertEqual(args[:3], ["marketing-studio", "dtc-ads", "generate"])
        for flag, val in (("--format-id", HEADLINE), ("--brand-kit-id", KIT), ("--quality", "low"),
                          ("--aspect-ratio", "4:5"), ("--resolution", "1k"), ("--batch-size", "1")):
            self.assertEqual(args[args.index(flag) + 1], val)
        media = [args[i + 1] for i, a in enumerate(args) if a == "--media"]
        self.assertEqual(media, [f"{MEDIA_A}:image", f"{MEDIA_B}:product"])
        self.assertIn("--wait", args)
        self.assertNotIn("compiled prompt", r["request_sha256"])

    def test_charge_reported_by_job(self):
        self.cli.routes["dtc.generate.low"] = fx("dtc_ads.generate.low.charged.response.json")
        ch = self.dtc("low")[0]["charge"]
        self.assertEqual((ch["certainty"], ch["credits"]), ("provider_reported", "1"))

    def test_charge_estimate_uses_cost_only(self):
        self.cli.balances = []
        ch = self.dtc("high", references=[MEDIA_A])[0]["charge"]
        self.assertEqual((ch["certainty"], ch["credits"]), ("estimated", "6.5"))
        cost_args = next(a for a, _ in self.cli.calls if self.cli.key(a) == "dtc.cost.high")
        self.assertIn("--cost-only", cost_args)
        self.assertNotIn("--wait", cost_args)

    def test_final_tier_uses_approved_draft_as_reference(self):
        out = self.dtc("high", references=[{"id": MEDIA_A, "role": "image"}])
        self.assertEqual(out[0]["tier"], "final")
        self.assertEqual(out[0]["reference_media_ids"], [MEDIA_A])

    def test_local_reference_is_uploaded_through_existing_upload(self):
        p = os.path.join(self.tmp.name, "draft.png")
        open(p, "wb").close()
        out = self.dtc("high", references=[{"path": "draft.png", "role": "image"}], job_dir=self.tmp.name)
        uploaded = fx("upload.create.response.json")["id"]
        self.assertEqual(out[0]["reference_media_ids"], [uploaded])
        up_args, up_env = self.cli.calls[0]
        self.assertEqual(up_args[:3], ["upload", "create", p])
        self.assertEqual(up_env["HIGGSFIELD_CREDENTIALS_PATH"], self.cred_file)

    def test_url_reference_downloads_via_resolve_params(self):
        # URL references take the shared _resolve_params path (urlretrieve), which the guard blocks here.
        with self.assertRaises(NetworkUsed):
            self.dtc("low", references=["https://cdn.example.test/a.png"])

    def test_cost_estimate_is_cost_only_and_never_uploads(self):
        p = os.path.join(self.tmp.name, "draft.png")
        open(p, "wb").close()
        est = hf.dtc_ad_cost(HEADLINE, "p", tier="final", references=[MEDIA_A, p], credential=self.credential(),
                             catalogue=self.cat)
        self.assertEqual(est["cost"]["credits"], 6.5)
        self.assertEqual(self.cli.ops(), ["dtc.cost.high"])
        args = self.cli.calls[0][0]
        self.assertEqual([args[i + 1] for i, a in enumerate(args) if a == "--media"], [f"{MEDIA_A}:image"])

    def test_catalogue_limits_and_refusals(self):
        self.refused("references_exceed_limit", self.dtc, "low", references=[MEDIA_A] * 15)
        self.refused("ad_format_unknown", hf.dtc_ad_generate, "00000000-0000-4000-8000-000000000000", KIT, None, [],
                     "low", prompt="p", credential=self.credential(), catalogue=self.cat)
        self.refused("quality_unknown", self.dtc, "ultra")
        self.refused("aspect_ratio_unsupported", self.dtc, "low", aspect_ratio="21:9")
        self.refused("batch_size_invalid", self.dtc, "low", batch_size=21)
        self.assertEqual(self.cli.calls, [])

    def test_failed_job_is_typed_and_carries_charge(self):
        self.cli.routes["dtc.generate.low"] = fx("dtc_ads.generate.failed.response.json")
        e = self.refused("generation_not_completed", self.dtc, "low")
        self.assertIn("balance_delta", e.detail)

    def test_ad_formats_from_catalogue_without_cli(self):
        self.assertEqual({f["id"] for f in hf.ad_formats(self.cat)}, set(self.cat.formats))
        self.assertEqual([f["name"] for f in hf.ad_formats(self.cat, type="headline")], ["Headline"])
        self.assertEqual(self.cli.calls, [])

    def test_catalogue_validation(self):
        with mock.patch.dict(os.environ, {hf.CATALOGUE_ENV: ""}):
            self.refused("provider_catalogue_missing", hf.load_catalogue, None)
        with mock.patch.dict(os.environ, {hf.CATALOGUE_ENV: CATALOGUE}):
            self.assertEqual(hf.load_catalogue().sha256, self.cat.sha256)
        raw = fx("catalogue.fixture.json")
        raw["quality_tiers"] = {"low": "draft"}
        self.refused("provider_catalogue_invalid", hf.parse_catalogue, raw)
        raw = fx("catalogue.fixture.json")
        del raw["limits"]["source"]["references_max"]
        self.refused("provider_catalogue_invalid", hf.parse_catalogue, raw)
        raw = fx("catalogue.fixture.json")
        raw["provider"] = "higgsfield_marketing"
        self.refused("provider_catalogue_invalid", hf.parse_catalogue, raw)


class TestBrandKit(Base):
    def test_valid_payload_refused_honestly_without_cli_call(self):
        e = self.refused("provider_operation_unavailable", hf.brand_kit_upsert, fx("brand_kit_payload.json"),
                         credential=self.credential())
        self.assertIn("brand_kit_id", e.detail)
        self.assertEqual(self.cli.calls, [])

    def test_credential_checked_before_operation(self):
        self.refused("provider_credentials_missing", hf.brand_kit_upsert, fx("brand_kit_payload.json"))

    def test_url_fetch_is_never_the_source_of_truth(self):
        self.refused("brand_kit_url_source_forbidden", hf.brand_kit_upsert, {"url": "https://example-home.test"},
                     credential=self.credential())
        payload = fx("brand_kit_payload.json")
        payload["source_url"] = "https://example-home.test"
        self.refused("brand_kit_url_source_forbidden", hf.brand_kit_upsert, payload, credential=self.credential())

    def test_invalid_payload_fields(self):
        for mutate in (lambda p: p.pop("fonts"), lambda p: p.__setitem__("colours", [{"hex": "blue", "role": "x"}]),
                       lambda p: p.__setitem__("logo_url", "http://insecure.test/logo.png"),
                       lambda p: p.__setitem__("values", "not-a-list")):
            payload = fx("brand_kit_payload.json")
            mutate(payload)
            self.refused("brand_kit_payload_invalid", hf.brand_kit_upsert, payload, credential=self.credential())


class TestExecutePlanDispatch(Base):
    def plan(self, **kw):
        p = {"schema_version": "provider-execution-plan-v1", "id": "plan-1", "provider": "higgsfield",
             "model": HEADLINE, "method": hf.MARKETING_STUDIO_METHOD,
             "params": {"format_id": HEADLINE, "brand_kit_id": KIT, "quality": "low"},
             "prompt": {"positive": "compiled positive prompt", "negative": None},
             "output": {"aspect_ratio": "9:16", "resolution": "2k", "count": 1},
             "media_inputs": [{"id": MEDIA_A, "role": "image"}]}
        p.update(kw)
        return p

    def test_method_selects_marketing_studio(self):
        out = hf.execute_plan(self.plan(), None, credential=self.credential(), catalogue=self.cat)
        self.assertEqual(out[0]["surface"], "marketing_studio")
        args = next(a for a, _ in self.cli.calls if self.cli.key(a) == "dtc.generate.low")
        self.assertEqual(args[args.index("--aspect-ratio") + 1], "9:16")
        self.assertEqual(args[args.index("--prompt") + 1], "compiled positive prompt")

    def test_marketing_studio_dry_run_and_cost_method(self):
        hf.execute_plan(self.plan(), None, ".", True, credential=self.credential(), catalogue=self.cat)
        hf.execute_plan(self.plan(method=hf.MARKETING_STUDIO_COST_METHOD), None, credential=self.credential(),
                        catalogue=self.cat)
        self.assertEqual(self.cli.ops(), ["dtc.cost.low", "dtc.cost.low"])

    def test_classic_plan_unchanged(self):
        plan = {"provider": "higgsfield", "model": "example_image_model", "method": "generate",
                "params": {"model_type": "fast"}, "prompt": {"positive": "p", "negative": "blur"},
                "output": {"aspect_ratio": "1:1", "count": 2}}
        hf.execute_plan(plan, None, ".", True, credential=self.credential())
        args = self.cli.calls[-1][0]
        self.assertEqual(args[:3], ["generate", "cost", "example_image_model"])
        self.assertIn("--batch_size", args)
        self.assertEqual(args[args.index("--model_type") + 1], "fast")

    def test_marketing_plan_requires_quality(self):
        self.refused("quality_unknown", hf.execute_plan, self.plan(params={"format_id": HEADLINE}), None,
                     credential=self.credential(), catalogue=self.cat)


if __name__ == "__main__":
    unittest.main()
