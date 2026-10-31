"""CS-B2 unit tests (no database). SQL evidence lives in test_b2_real_db.py."""
from __future__ import annotations

import copy
import json
import subprocess
import unittest
from pathlib import Path

from service.brand.errors import InputRefusal
from service.brand.mos_brandkit import build_package, package_version_ref
from service.requests import compile_mapping, contracts, documents, gen_types, mos_intake
from service.requests.canonical import CanonicalError, artifact_sha256, canonical_text, loads_strict
from tests.creative_studio import b2_contract_fixtures, b2_mos_fixtures as fx
from tests.creative_studio.test_cs_b1_unit import FORBIDDEN_LITERALS

ROOT = Path(__file__).resolve().parents[2]
B2_FILES = (sorted((ROOT / "service" / "brand").glob("*.py")) + sorted((ROOT / "service" / "requests").glob("*.py"))
            + sorted((ROOT / "tests" / "creative_studio").glob("*b2*.py"))
            + sorted((ROOT / "tests" / "creative_studio" / "b2_fixtures").glob("*.json"))
            + [ROOT / "supabase" / "migrations" / "20261031140100_cs_b2_brand_campaign_inputs.sql"])
B1_BASE = "37de2c7"


class CanonicalJsonTests(unittest.TestCase):
    def test_rfc8785_reference_example(self):
        # RFC 8785 §3.2.2 example input and its canonical output
        raw = ('{"numbers":[333333333.33333329,1E30,4.50,2e-3,0.000000000000000000000000001],'
               '"string":"\\u20ac$\\u000F\\u000aA\'\\u0042\\u0022\\u005c\\\\\\"\\/","literals":[null,true,false]}')
        expected = ('{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
                    '"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}')
        self.assertEqual(canonical_text(loads_strict(raw)), expected)

    def test_rfc8785_key_order_is_utf16(self):
        obj = {"€": "Euro", "\r": "CR", "דּ": "Hebrew", "1": "One", "\U0001F600": "Emoji",
               "\u0080": "Control", "ö": "Latin"}
        self.assertEqual(list(json.loads(canonical_text(obj))),
                         ["\r", "1", "\u0080", "ö", "€", "\U0001F600", "דּ"])

    def test_numbers(self):
        for value, text in ((0.0, "0"), (-0.0, "0"), (1e21, "1e+21"), (1e20, "100000000000000000000"),
                            (1e-7, "1e-7"), (1e-6, "0.000001"), (123.0, "123"), (-1.5, "-1.5"),
                            (2 ** 53 - 1, "9007199254740991")):
            self.assertEqual(canonical_text(value), text, value)

    def test_refusals(self):
        for bad in (float("nan"), float("inf"), 2 ** 53, "\ud800", {1: "x"}, {"a": {1, 2}}):
            with self.assertRaises(CanonicalError, msg=repr(bad)):
                canonical_text(bad)
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', '[Infinity]'):
            with self.assertRaises(CanonicalError, msg=raw):
                loads_strict(raw)

    def test_artifact_digest_omits_only_top_level_sha(self):
        doc = {"b": 1, "sha256": "x" * 64, "nested": {"sha256": "kept"}}
        self.assertEqual(artifact_sha256(doc), artifact_sha256({**doc, "sha256": "y" * 64}))
        self.assertNotEqual(artifact_sha256(doc), artifact_sha256({**doc, "nested": {"sha256": "changed"}}))


class ContractTests(unittest.TestCase):
    def test_generated_types_are_current(self):
        self.assertEqual(gen_types.OUT.read_text(encoding="utf-8"), gen_types.generate(),
                         "run: python3 -m service.requests.gen_types")

    def test_published_fixtures_are_current_and_valid(self):
        fresh = b2_contract_fixtures.generate()
        committed = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in b2_contract_fixtures.OUT_DIR.glob("*.json")}
        self.assertEqual(set(committed), set(fresh), "run: python3 -m tests.creative_studio.b2_contract_fixtures")
        for name, doc in committed.items():
            self.assertEqual(doc, fresh[name], name)
            contracts.validate(doc)
            self.assertEqual(doc["sha256"], artifact_sha256(doc), name)

    def test_unknown_fields_and_formats_refused(self):
        doc = json.loads((b2_contract_fixtures.OUT_DIR / "cs-treatment-v1.home-services.draft.json").read_text())
        for mutate, pointer in ((lambda d: d.update(extra=1), ""), (lambda d: d.update(created_at="yesterday"),
                                                                    "/created_at"),
                                (lambda d: d.update(schema_version="cs-treatment-v9"), "/schema_version")):
            bad = copy.deepcopy(doc)
            mutate(bad)
            with self.assertRaises(contracts.ContractViolation) as ctx:
                contracts.validate(bad, "cs-treatment-v1")
            self.assertIn(pointer or "/", ctx.exception.field_paths)


class BrandAdapterTests(unittest.TestCase):
    def build(self, mutate=None, key="unit-home"):
        biz, _ = fx.home_services(key)
        if mutate:
            mutate(biz)
        return build_package(biz.export())

    def refused(self, mutate, code):
        with self.assertRaises(InputRefusal) as ctx:
            self.build(mutate)
        self.assertEqual(ctx.exception.code, code)

    def test_approved_kit_is_deterministic_and_valid(self):
        a, b = self.build(), self.build()
        self.assertEqual(a.document, b.document)
        contracts.validate(a.document)
        self.assertEqual(a.document["approval"]["policy_revision"], "mos-envelope-approval-v1",
                         "owner/envelope approval exported as-is, no invented independent review")
        self.assertEqual({l["role"] for l in a.document["logos"]}, {"primary", "monochrome"})
        self.assertTrue(all(l["allow_crop"] is False and l["allow_distort"] is False for l in a.document["logos"]))

    def test_typed_refusals(self):
        self.refused(lambda b: setattr(b, "status", "draft"), "brand_not_approved")
        self.refused(lambda b: setattr(b, "current", {"version_id": fx.uid("x"), "revision": 2, "status": "approved"}),
                     "approval_stale")
        self.refused(lambda b: b.content.pop("logo_usage"), "brandkit_incomplete")
        self.refused(lambda b: b.content["tone_attributes"].update(value=[]), "brandkit_incomplete")
        self.refused(lambda b: [b.files.remove(f) for f in [f for f in b.files if f["kind"] == "font"]],
                     "font_asset_missing")
        self.refused(lambda b: setattr(b, "approval", lambda: {**fx.Business.approval(b), "actor_role": "edited"}),
                     "approval_receipt_invalid")
        self.refused(lambda b: setattr(b, "higgsfield_payload",
                                       lambda: {**fx.Business.higgsfield_payload(b), "logo_sha256": "0" * 64}),
                     "higgsfield_payload_mismatch")
        self.refused(lambda b: b.content["palette"]["value"].clear(), "brandkit_incomplete")


class IntakeAndMappingTests(unittest.TestCase):
    def ctx(self, factory=fx.home_services, key=None):
        biz, camp = factory(key) if key else factory()
        doc = build_package(biz.export()).document
        return biz, camp, {"document": doc, "ref": package_version_ref(doc), "source_artifact_id": biz.artifact_id}

    def fields(self, camp, pkg, **vr):
        return mos_intake.build_request_fields(camp.visual_request(**vr), camp.sources(), pkg)

    def test_canvas_presets_and_refusals(self):
        _, camp, pkg = self.ctx()
        for canvas, preset in (("1:1", "square_1080"), ("4:5", "portrait_1080x1350"), ("9:16", "story_1080x1920")):
            self.assertEqual(self.fields(camp, pkg, canvas=canvas)["formats"][0]["preset"], preset)
        for canvas in ("16:9", "3:2"):
            with self.assertRaises(InputRefusal) as ctx:
                self.fields(camp, pkg, canvas=canvas)
            self.assertEqual(ctx.exception.code, "unsupported_canvas")
        with self.assertRaises(InputRefusal) as ctx:
            self.fields(camp, pkg, tier="final")
        self.assertEqual(ctx.exception.code, "source_unavailable", "final tier needs the signed-off draft")

    def test_copy_claims_budget(self):
        _, camp, pkg = self.ctx()
        f = self.fields(camp, pkg)
        self.assertEqual(f, self.fields(camp, pkg), "deterministic in its inputs")
        self.assertTrue(all(c["id"].startswith("v1-") for c in f["exact_copy"]), "flagged variant excluded")
        self.assertTrue(all(c["claim_refs"] for c in f["exact_copy"]))
        camp.claims = {}
        with self.assertRaises(InputRefusal) as ctx:
            self.fields(camp, pkg)
        self.assertEqual(ctx.exception.code, "claim_unsourced")
        _, camp, pkg = self.ctx()
        src = camp.sources()
        src.pop("budget")
        with self.assertRaises(InputRefusal) as ctx:
            mos_intake.build_request_fields(camp.visual_request(), src, pkg)
        self.assertEqual(ctx.exception.code, "budget_missing")
        _, camp, pkg = self.ctx()
        pkg = {**pkg, "source_artifact_id": fx.uid("other-brandkit")}
        with self.assertRaises(InputRefusal) as ctx:
            self.fields(camp, pkg)
        self.assertEqual(ctx.exception.code, "approval_stale")

    def _chain(self, factory):
        biz, camp, pkg = self.ctx(factory)
        fields = self.fields(camp, pkg)
        fields["origin"]["generation_eligibility"] = b2_contract_fixtures._eligibility(camp, camp.sources(), fields)
        ws, actor = fx.uid("studio-ws", "unit"), fx.uid("user", "unit")
        req = documents.new_request(fields, workspace_id=ws, actor_id=actor)
        tr = documents.new_treatment(mos_intake.build_treatment_fields(camp.sources(),
                                     documents.version_ref(req, "creative-request"), pkg["ref"]),
                                     workspace_id=ws, actor_id=actor)
        return camp, pkg, req, tr

    def test_service_lane_has_no_geometry(self):
        for factory in (fx.home_services, fx.physio_clinic):
            camp, pkg, req, tr = self._chain(factory)
            with self.assertRaises(InputRefusal) as ctx:
                compile_mapping.compile_inputs(req, tr, pkg["document"])
            self.assertEqual(ctx.exception.code, "treatment_not_accepted")
            acc = documents.accepted_treatment(tr, actor_id=tr["created_by"])
            out = compile_mapping.compile_inputs(req, acc, pkg["document"])
            self.assertEqual(out["lane"], "studio-offering")
            text = json.dumps(out).lower()
            for banned in ("geometry", "preserve exact product", "proportions", "product_spec_id"):
                self.assertNotIn(banned, text)
            bad = copy.deepcopy(req)
            bad["offering"]["physical_product_spec"] = fx.kitchenware()[1].offering["physical_product_spec"]
            with self.assertRaises(InputRefusal) as ctx:
                compile_mapping.compile_inputs(bad, {**acc, "source_request": {**acc["source_request"]}},
                                               pkg["document"])
            self.assertIn(ctx.exception.code, ("offering_geometry_forbidden", "source_revision_changed"))
            with self.assertRaises(contracts.ContractViolation):
                contracts.validate({**bad, "sha256": artifact_sha256(bad)})   # schema also forbids it

    def test_physical_lane_uses_unchanged_legacy_compiler(self):
        camp, pkg, req, tr = self._chain(fx.kitchenware)
        acc = documents.accepted_treatment(tr, actor_id=tr["created_by"])
        with self.assertRaises(InputRefusal) as ctx:
            compile_mapping.compile_inputs(req, acc, pkg["document"])
        self.assertEqual(ctx.exception.code, "physical_product_spec_required")
        with self.assertRaises(InputRefusal) as ctx:
            compile_mapping.compile_inputs(req, acc, pkg["document"], product_spec={**camp.product_spec, "name": "x"})
        self.assertEqual(ctx.exception.code, "asset_hash_mismatch")
        out = compile_mapping.compile_inputs(req, acc, pkg["document"], product_spec=camp.product_spec)
        self.assertEqual(out["lane"], "studio-physical")
        self.assertIn("Preserve exact product geometry", out["provider_prompt"]["positive"])


class HygieneTests(unittest.TestCase):
    def test_no_unseen_set_literals(self):
        for path in B2_FILES:
            text = path.read_text(encoding="utf-8").lower()
            for literal in FORBIDDEN_LITERALS:
                self.assertNotIn(literal, text, f"{literal!r} in {path.relative_to(ROOT)}")

    def test_legacy_compiler_and_b1_files_untouched(self):
        try:
            out = subprocess.run(["git", "diff", "--name-only", B1_BASE, "--", "core", "schemas", "knowledge",
                                  "service/auth.py", "service/config.py", "service/db.py", "service/errors.py",
                                  "service/storage.py", "service/worker.py", "api",
                                  "supabase/migrations/20261030210000_cs_b1_platform_foundation.sql",
                                  "tests/creative_studio/pg_harness.py", "tests/creative_studio/fake_storage.py",
                                  "tests/creative_studio/test_cs_b1_real_db.py", "tests/creative_studio/test_cs_b1_unit.py"],
                                 cwd=ROOT, capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git history unavailable")
        self.assertEqual(out.strip(), "")
        diff = subprocess.run(["git", "diff", B1_BASE, "--", "service/api.py"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout
        added = [l for l in diff.splitlines() if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
        self.assertTrue(all(l.startswith("+") for l in added), "api.py: additions only")
        self.assertIn("+# --- CS-B2 routes ---", added)
        self.assertEqual(added[-1], "+# --- end CS-B2 routes ---")

    def test_no_security_definer_and_no_network_in_b2(self):
        sql = (ROOT / "supabase/migrations/20261031140100_cs_b2_brand_campaign_inputs.sql").read_text().lower()
        self.assertNotIn("security definer", sql)
        for path in B2_FILES:
            if path.suffix == ".py" and "service" in path.parts and path.name != "importer.py":
                self.assertFalse("urlopen" in path.read_text(), f"network call in {path.name}")
