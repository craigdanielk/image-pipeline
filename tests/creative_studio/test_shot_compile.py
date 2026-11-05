"""Shot-spec compile branch (creative catalogue) and the legacy-prose regression guard."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from service.brand.errors import InputRefusal
from service.catalogue import CatalogueError, get_format, get_shot, load_catalogue
from service.catalogue.creative import shot_prompt
from service.requests import compile_mapping, contracts, mos_intake

LEGACY = Path(__file__).parent / "fixtures" / "legacy_compile_b904f16.json"
CAT = load_catalogue()
REGENIC_SLOTS = {
    "subject": "A South African man in his early 30s with short natural hair",
    "action": "looks at his phone on a light linen sofa",
    "setting": "a calm, sunlit apartment living room",
    "expression": "relaxed and quietly relieved",
}
REGENIC_CLIENT = {
    "scene_palette": [{"hex": "#F6F5EE", "object": "warm off-white walls"},
                      {"hex": "#152037", "object": "one deep navy cushion"},
                      {"hex": "#3B82F6", "object": "one small soft-blue mug"}],
    "style_line": "Natural, warm, discreet photography of real people living full lives; calm and confident.",
    "people_policy": "buyer_models",
    "restrictions": ["No before-and-after comparisons or side-by-side body or skin images.",
                     "No close-ups that single out or pinch a body area, and nothing implying the viewer's appearance is inferior.",
                     "No medicine names, pill packaging, syringes or prescription products in the image.",
                     "Results imagery may not imply a guaranteed or typical outcome."],
    "people_policy_note": None,
}
BANNED = ("Proposed:", "atmosphere", "Secondary text", "logo misuse", "Finasteride", "Brand palette:", "Offering:")


def _spec(**over):
    spec = {"schema_version": "mos-shot-spec-v1", "catalogue_version": CAT["version"], "format_id": "feed_single_image",
            "shot_id": "lifestyle_moment", "canvas": "4:5", "slots": dict(REGENIC_SLOTS),
            "client": {k: v for k, v in REGENIC_CLIENT.items() if k != "people_policy_note"}, "references": []}
    spec.update(over)
    return spec


def _fmt(fid="feed_single_image"):
    return get_format(CAT, fid)


class LegacyCompileTests(unittest.TestCase):
    def test_legacy_concept_direction_unchanged(self):
        """A request without shot_spec compiles byte-identically to output captured before the shot_spec branch."""
        fx = json.loads(LEGACY.read_text())
        out = compile_mapping.compile_inputs(fx["request"], fx["treatment"], fx["package"])
        self.assertEqual(out, fx["expected"])
        self.assertNotIn("shot_spec", fx["request"])


class ShotPromptTests(unittest.TestCase):
    def prompt(self, shot_id="lifestyle_moment", slots=None, canvas="4:5", fmt="feed_single_image", client=None):
        return shot_prompt(get_shot(CAT, shot_id), REGENIC_SLOTS if slots is None else slots,
                           client or REGENIC_CLIENT, canvas, fmt=_fmt(fmt) if fmt else None)

    def test_regenic_lifestyle_prompt_is_short_and_concrete(self):
        p = self.prompt()
        self.assertLessEqual(len(p["positive"]), 1200)
        self.assertLessEqual(len(p["negative"]), 400)
        self.assertIn("35mm", p["positive"])
        self.assertIn(REGENIC_SLOTS["subject"], p["positive"])
        self.assertIn("Colour cues: warm off-white walls in #F6F5EE, one deep navy cushion in #152037", p["positive"])
        self.assertIn("Upper-left third is plain and empty for a headline", p["positive"])
        for banned in BANNED:
            self.assertNotIn(banned, p["positive"])
        self.assertNotIn("{", p["positive"])

    def test_restrictions_go_to_negative_only(self):
        p = self.prompt()
        self.assertIn("before-and-after comparisons", p["negative"])
        self.assertIn("medicine names", p["negative"])
        self.assertNotIn("before-and-after", p["positive"])
        self.assertEqual(p["negative"].count("plastic skin"), 1, "deduped")

    def test_text_space_rules(self):
        self.assertIn("Leave one clean corner for a small logo", self.prompt(fmt="library_plate", canvas="1:1")["positive"])
        self.assertNotIn("headline", self.prompt(fmt="library_plate", canvas="1:1")["positive"])
        zones = {"top": "Upper", "bottom": "Lower", "left": "Left"}
        for shot in CAT["shots"]:
            if shot["text_zone"] == "none" and shot["people"] != "owner_photo_only" and "{text_space}" in shot["prompt_template"]:
                slots = {s["key"]: "x" * s["min_chars"] for s in shot["slots"]}
                self.assertIn("Leave one clean corner for a small logo",
                              shot_prompt(shot, slots, REGENIC_CLIENT, "1:1")["positive"])
        self.assertIn(zones["left"], self.prompt("outcome_confidence")["positive"])

    def test_refusals(self):
        def code(**kw):
            with self.assertRaises(CatalogueError) as ctx:
                self.prompt(**kw)
            return ctx.exception
        self.assertEqual(code(slots={**REGENIC_SLOTS, "mood": "calm"}).code, "slot_invalid")
        self.assertIn("mood", code(slots={**REGENIC_SLOTS, "mood": "calm"}).message)
        missing = {k: v for k, v in REGENIC_SLOTS.items() if k != "action"}
        self.assertEqual(code(slots=missing).code, "slot_invalid")
        self.assertIn("action", code(slots=missing).message)
        self.assertEqual(code(slots={**REGENIC_SLOTS, "action": "x"}).code, "slot_invalid")
        self.assertEqual(code(slots={**REGENIC_SLOTS, "action": "x" * 500}).code, "slot_invalid")
        self.assertEqual(code(canvas="16:9").code, "canvas_invalid")
        self.assertEqual(code(shot_id="founder_team", slots={"subject": "x" * 20, "setting": "y" * 10}).code,
                         "shot_not_generatable")

    def test_enum_slot_refused(self):
        shot = copy.deepcopy(get_shot(CAT, "lifestyle_moment"))
        shot["slots"][3]["enum"] = ["calm", "joyful"]
        with self.assertRaises(CatalogueError) as ctx:
            shot_prompt(shot, REGENIC_SLOTS, REGENIC_CLIENT, "4:5")
        self.assertEqual(ctx.exception.code, "slot_invalid")
        self.assertIn("expression", ctx.exception.message)

    def test_hard_caps_not_truncated(self):
        long_client = {**REGENIC_CLIENT, "restrictions": ["No " + "z" * 150] * 1 + ["No " + "y" * 150, "No " + "x" * 150]}
        with self.assertRaises(CatalogueError) as ctx:
            self.prompt(client=long_client)
        self.assertEqual(ctx.exception.code, "prompt_too_long")
        long_style = {**REGENIC_CLIENT, "style_line": "s" * 1200}
        with self.assertRaises(CatalogueError) as ctx:
            self.prompt(client=long_style)
        self.assertEqual(ctx.exception.code, "prompt_too_long")

    def test_negative_cut_at_phrase_boundary(self):
        many = {**REGENIC_CLIENT, "restrictions": REGENIC_CLIENT["restrictions"][:1]}
        shot = copy.deepcopy(get_shot(CAT, "lifestyle_moment"))
        shot["negative"] = [f"unwanted thing number {i}" for i in range(60)]
        n = shot_prompt(shot, REGENIC_SLOTS, many, "4:5")["negative"]
        self.assertLessEqual(len(n), 400)
        self.assertIn("before-and-after comparisons", n)
        self.assertFalse(n.endswith(("number 1", "numb")) and n.split(", ")[-1] not in shot["negative"])


class CompileShotSpecTests(unittest.TestCase):
    def chain(self, spec):
        fx = json.loads(LEGACY.read_text())
        req = {**fx["request"], "shot_spec": spec}
        return req, fx["treatment"], fx["package"]

    def test_compile_inputs_uses_shot_spec_without_brand_prose(self):
        req, tr, pkg = self.chain(_spec())
        out = compile_mapping.compile_inputs(req, tr, pkg)
        pp = out["provider_prompt"]
        for banned in BANNED:
            self.assertNotIn(banned, pp["positive"])
        self.assertLessEqual(len(pp["positive"]), 1200)
        self.assertLessEqual(len(pp["negative"]), 400)
        self.assertIn("35mm", pp["positive"])
        md = pp["metadata"]
        self.assertEqual((md["shot_id"], md["format_id"], md["catalogue_version"]),
                         ("lifestyle_moment", "feed_single_image", CAT["version"]))
        self.assertEqual(set(out), set(json.loads(LEGACY.read_text())["expected"]))
        self.assertEqual(set(pp), set(json.loads(LEGACY.read_text())["expected"]["provider_prompt"]))

    def test_catalogue_version_mismatch_and_bad_shot_refused(self):
        for spec in (_spec(catalogue_version="0.0.1"), _spec(shot_id="nope"), _spec(slots={})):
            req, tr, pkg = self.chain(spec)
            with self.assertRaises(InputRefusal):
                compile_mapping.compile_inputs(req, tr, pkg)

    def test_shot_spec_changes_semantic_hash_and_validates(self):
        from service.requests.canonical import request_semantic_sha256
        fx = json.loads(LEGACY.read_text())
        req, _, _ = self.chain(_spec())
        self.assertNotEqual(request_semantic_sha256(req), request_semantic_sha256(fx["request"]))
        shot_spec_errors = [e for e in contracts.errors_for({**fx["request"], "shot_spec": req["shot_spec"]},
                                                              "cs-creative-request-v1") if "shot_spec" in e[0]]
        self.assertEqual(shot_spec_errors, [])
        bad = {**fx["request"], "shot_spec": {**req["shot_spec"], "extra": 1}}
        self.assertTrue([e for e in contracts.errors_for(bad, "cs-creative-request-v1") if "shot_spec" in e[0]])


class MosIntakeShotSpecTests(unittest.TestCase):
    def setUp(self):
        from service.brand.mos_brandkit import build_package, package_version_ref
        from tests.creative_studio import b2_mos_fixtures as fx
        self.fx = fx
        self.biz, self.camp = fx.home_services()
        doc = build_package(self.biz.export()).document
        self.pkg = {"document": doc, "ref": package_version_ref(doc), "source_artifact_id": self.biz.artifact_id}

    def test_mos_intake_accepts_shot_spec_and_library_shot_spec(self):
        from service.requests import documents
        from tests.creative_studio.test_mos_library_unit import library_request, library_sources
        spec = _spec()
        vr = self.camp.visual_request()
        plain = mos_intake.build_request_fields(vr, self.camp.sources(), self.pkg)
        self.assertNotIn("shot_spec", plain)
        got = mos_intake.build_request_fields({**vr, "shot_spec": spec}, self.camp.sources(), self.pkg)
        self.assertEqual(got["shot_spec"], spec)
        self.assertEqual({k: v for k, v in got.items() if k != "shot_spec"}, plain)
        with self.assertRaises(InputRefusal) as ctx:
            mos_intake.build_request_fields({**vr, "shot_spec": {**spec, "schema_version": "x"}},
                                            self.camp.sources(), self.pkg)
        self.assertEqual(ctx.exception.code, "unsupported_schema_version")

        # library path: shot_spec is carried, the request still validates, and the treatment keeps legacy direction
        binding = self.fx.uid("connection", "shot")
        src = library_sources(self.biz, self.camp)
        lib_spec = _spec(format_id="library_plate", canvas="1:1")
        lib = mos_intake.build_library_request_fields({**library_request(self.biz), "shot_spec": lib_spec}, src, self.pkg, binding)
        self.assertEqual(lib["shot_spec"], lib_spec)
        lib_plain = mos_intake.build_library_request_fields(library_request(self.biz), src, self.pkg, binding)
        self.assertNotIn("shot_spec", lib_plain)
        fx = self.fx
        lib["origin"]["generation_eligibility"] = fx._sealed({
            "id": fx.uid("eligibility", "shot"), "authority": "marketing-os", "workspace_id": self.biz.mos_ws,
            "action": "generate", "decision": "allowed", "actor_id": fx.uid("user", "producer"),
            "source_versions": [src["library"]], "policy": src["policy"],
            "approval_receipt_ids": [src["library_approval"]["receipt_id"]], "checked_at": "2026-10-31T08:59:30Z",
            "valid_until": "2026-10-31T09:00:30Z", "reason_codes": [], "sha256": "0" * 64})
        ws, actor = self.fx.uid("studio-ws", "shot"), self.fx.uid("user", "shot")
        req = documents.new_request(lib, workspace_id=ws, actor_id=actor)   # validates creative-request-v1
        tr = documents.new_treatment(mos_intake.build_library_treatment_fields(src, documents.version_ref(req, "creative-request"),
                                                                               self.pkg["ref"]), workspace_id=ws, actor_id=actor)
        self.assertEqual(tr["direction"]["summary"], self.camp.direction["summary"])
        acc = documents.accepted_treatment(tr, actor_id=tr["created_by"])
        out = compile_mapping.compile_inputs(req, acc, self.pkg["document"])
        self.assertIn("Leave one clean corner for a small logo", out["provider_prompt"]["positive"])
        self.assertEqual(out["provider_prompt"]["metadata"]["format_id"], "library_plate")


if __name__ == "__main__":
    unittest.main()
