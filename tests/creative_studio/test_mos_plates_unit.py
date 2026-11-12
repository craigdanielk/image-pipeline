"""Marketing OS video plates through the existing Creative Studio path (additive contract, 2026-11-13).

A plate (``mos-visual-request-v1`` with ``source: plates``) carries the user's assistant prompt in shot_spec.prompt and
the model the user agreed credits for. It becomes a creative-request-v1 with origin ``marketing-os-plates``, no copy and
no logo/text layers; auto model selection never picks a plate-only catalogue entry; its handoff carries the plate target.
Video plates are refused at intake until the runtime can store mp4 output. Fixture business only.
"""
from __future__ import annotations

import unittest

from service.brand.errors import InputRefusal
from service.brand.mos_brandkit import build_package, package_version_ref
from service.catalogue import load_catalogue
from service.composition.contracts import errors as schema_errors
from service.errors import ServiceError
from service.plans import compiler
from service.requests import compile_mapping, documents, mos_intake
from tests.creative_studio import b2_mos_fixtures as fx

CAT = load_catalogue()
KEY = "plate-evidence-01"
PROMPT = ("Macro photograph of loose dark hair strands on a crumpled cream linen pillowcase, soft early "
          "morning window light, shallow depth of field, natural film grain. No text, no logos, no signage.")


def plate_spec(**over):
    spec = {"schema_version": "mos-shot-spec-v1", "catalogue_version": CAT["version"], "format_id": "library_plate",
            "shot_id": "environment_scene", "canvas": "9:16",
            "slots": {"setting": "a quiet bedroom at dawn", "details": "a cream linen pillowcase with loose hair strands"},
            "client": {"scene_palette": [{"hex": "#F6F5EE", "object": "cream linen"}], "style_line": "Natural, calm photography.",
                       "people_policy": "none", "restrictions": ["No before-and-after comparisons."]},
            "references": [], "prompt": {"positive": PROMPT, "negative": "people, faces"}}
    spec.update(over)
    return spec


def plate_sources(biz, camp, **over) -> dict:
    ws = biz.mos_ws
    plate_set = fx._vref(ws, "PlateSet", "evidence")
    src = {
        "schema_version": "mos-plate-sources-v1", "workspace_id": ws, "plate_set": plate_set,
        "plate_set_approval": fx._sealed({"receipt_id": fx.uid("plate-agreement"), "authority": "marketing-os",
                                          "workspace_id": ws, "subject": plate_set, "decision": "approved",
                                          "actor_id": fx.uid("user", "owner"), "actor_role": "owner",
                                          "approved_at": fx.APPROVED_AT, "policy_revision": "mos-plate-credits-v1",
                                          "sha256": "0" * 64}),
        "plate": {"key": KEY, "kind": "image", "aspect": "9:16", "duration_s": None, "start_asset": None},
        "concept_direction": camp.direction, "brand_kit": {"id": biz.artifact_id, "version": 1},
        "policy": fx._vref(ws, "GenerationPolicy", "plates"), "offering": camp.offering,
        "audiences": [fx._vref(ws, "Avatar", "home-services")], "objective": camp.objective,
        "hook": "Hair falls for many reasons", "locale": "en-GB", "forbidden": ["No before-and-after comparisons."],
        "budget": {"currency": "USD", "max_cost_micros": 500_000, "funding_source": "workspace",
                   "policy": fx._vref(ws, "MediaCap", "workspace"), "expires_at": "2026-12-31T00:00:00.000Z"},
        "provider_selection": {"mode": "selected", "provider": "higgsfield", "model_id": "gpt_image_2_6", "quality": "standard",
                               "max_unit_cost_micros": 100_000},
    }
    src.update(over)
    return src


def plate_request(biz, **over) -> dict:
    req = {"schema_version": "mos-visual-request-v1", "station": "plates", "step": "plates_prepare",
           "workspace_id": biz.mos_ws, "source": "plates",
           "plates": {"set_id": fx.uid("PlateSet", "evidence"), "version": 1, "plate_key": KEY},
           "placement_id": None, "concept_id": None, "tier": "draft", "media_kind": "image", "canvas": "9:16", "slot": 1,
           "reference": None, "copy_refs": [], "content_pack_ref": None, "production_script": None,
           "shot_spec": plate_spec(), "composition": {"exact_layers": "none", "text_model_drawn": False}}
    req.update(over)
    return req


class PlatesIntake(unittest.TestCase):
    def setUp(self):
        self.biz, self.camp = fx.home_services()
        doc = build_package(self.biz.export()).document
        self.pkg = {"document": doc, "ref": package_version_ref(doc), "source_artifact_id": self.biz.artifact_id}
        self.binding = fx.uid("connection", "plates")

    def fields(self, visual=None, sources=None):
        return mos_intake.build_plates_request_fields(visual or plate_request(self.biz),
                                                      sources or plate_sources(self.biz, self.camp), self.pkg, self.binding)

    def refused(self, code, **kw):
        with self.assertRaises(InputRefusal) as ctx:
            self.fields(**kw)
        self.assertEqual(ctx.exception.code, code)

    def saved_request(self):
        f = self.fields()
        src = plate_sources(self.biz, self.camp)
        f["origin"]["generation_eligibility"] = fx._sealed({
            "id": fx.uid("eligibility", "plates"), "authority": "marketing-os", "workspace_id": self.biz.mos_ws,
            "action": "generate", "decision": "allowed", "actor_id": fx.uid("user", "owner"),
            "source_versions": [src["plate_set"]], "policy": src["policy"],
            "approval_receipt_ids": [src["plate_set_approval"]["receipt_id"]], "checked_at": "2026-10-31T08:59:30Z",
            "valid_until": "2026-10-31T09:00:30Z", "reason_codes": [], "sha256": "0" * 64})
        ws, actor = fx.uid("studio-ws", "plates"), fx.uid("user", "owner")
        req = documents.new_request(f, workspace_id=ws, actor_id=actor)          # validates creative-request-v1
        tr = documents.new_treatment(mos_intake.build_plates_treatment_fields(src, documents.version_ref(req, "creative-request"),
                                                                              self.pkg["ref"]), workspace_id=ws, actor_id=actor)
        return req, tr

    def test_plate_request_validates_without_copy_and_names_its_model(self):
        req, tr = self.saved_request()
        self.assertEqual(req["origin"]["kind"], "marketing-os-plates")
        self.assertEqual(req["origin"]["plate_key"], KEY)
        self.assertEqual(req["exact_copy"], [])
        self.assertEqual(req["formats"][0]["channel"], "plates")
        self.assertEqual(req["provider_selection"]["model_id"], "gpt_image_2_6")
        self.assertEqual(req["budget"]["credential_binding_id"], self.binding)
        self.assertEqual(tr["sources"][0], plate_sources(self.biz, self.camp)["plate_set"])

    def test_assistant_prompt_compiles_verbatim(self):
        req, tr = self.saved_request()
        tr = documents.accepted_treatment(tr, actor_id=fx.uid("user", "owner"))
        out = compile_mapping.compile_inputs(req, tr, self.pkg["document"])
        self.assertEqual(out["provider_prompt"]["positive"], PROMPT)
        self.assertEqual(out["provider_prompt"]["metadata"]["prompt_written_by"], "assistant")
        self.assertIn("before-and-after", out["provider_prompt"]["negative"])

    def test_refusals(self):
        src = plate_sources(self.biz, self.camp)
        self.refused("source_revision_changed", visual=plate_request(self.biz, plates={"plate_key": "plate-other"}))
        self.refused("unsupported_canvas", visual=plate_request(self.biz, canvas="4:5"))
        self.refused("unsupported_canvas", visual=plate_request(self.biz, tier="final"))
        self.refused("prompt_required", visual=plate_request(self.biz, shot_spec=plate_spec(prompt=None)))
        self.refused("budget_missing", sources={k: v for k, v in src.items() if k != "budget"})
        self.refused("invalid_contract", sources={**src, "provider_selection": None})
        self.refused("approval_stale", sources={**src, "brand_kit": {"id": fx.uid("other-brandkit"), "version": 1}})
        self.refused("scope_mismatch", visual=plate_request(self.biz, workspace_id=fx.uid("other-ws")))
        video = {**src["plate"], "kind": "video", "duration_s": 5}
        self.refused("video_plates_unavailable", visual=plate_request(self.biz, media_kind="video"),
                     sources={**src, "plate": video})
        self.assertTrue(mos_intake.is_plates_request(plate_request(self.biz)))
        self.assertFalse(mos_intake.is_plates_request(self.camp.visual_request()))


def _entry(entry_id, model_id, cost, selection=None):
    snap = {"selection": selection} if selection else {}
    return {"entry_id": entry_id, "provider": "higgsfield", "model_id": model_id, "tier": "draft",
            "credential_modes": ["workspace"], "unit_cost_micros": cost, "price_snapshot": snap}


class ExplicitOnlySelection(unittest.TestCase):
    CAT = [_entry("hf-nano-banana-2-1-draft", "nano_banana_2_1", 93_750),
           _entry("hf-gpt-image-2-6-plate", "gpt_image_2_6", 31_250, "explicit")]

    def req(self, model_id=None):
        return {"provider_selection": {"provider": None, "model_id": model_id, "max_unit_cost_micros": None},
                "budget": {"funding_source": "workspace"}}

    def test_auto_selection_never_picks_a_plate_only_entry(self):
        self.assertEqual(compiler.select_entry(self.CAT, self.req(), "draft")["entry_id"], "hf-nano-banana-2-1-draft",
                         "post pictures keep their model even though the plate model is cheaper")

    def test_a_plate_names_its_model(self):
        self.assertEqual(compiler.select_entry(self.CAT, self.req("gpt_image_2_6"), "draft")["entry_id"],
                         "hf-gpt-image-2-6-plate")
        with self.assertRaises(ServiceError):
            compiler.select_entry(self.CAT, self.req("kling3_0"), "draft")


class PlateHandoffTarget(unittest.TestCase):
    def test_handoff_schema_accepts_the_plate_target(self):
        from service.composition import contracts as cc
        schema = cc._schema("cs-mos-handoff-v1") if hasattr(cc, "_schema") else None
        target = {"authority": "marketing-os", "workspace_id": fx.uid("mos-ws"),
                  "plate_set": fx._vref(fx.uid("mos-ws"), "PlateSet", "evidence"), "plate_key": KEY,
                  "policy": fx._vref(fx.uid("mos-ws"), "GenerationPolicy", "plates")}
        if schema is None:
            self.skipTest("handoff target is checked through build_handoff in the B4 suite")
        self.assertEqual(schema_errors({"target": target}, "cs-mos-handoff-v1", pointer="/properties/target"), [])


if __name__ == "__main__":
    unittest.main()
