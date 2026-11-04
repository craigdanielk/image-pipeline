"""Marketing OS image library through the existing Creative Studio path (additive contract, 2026-11-05).

A LibraryPack visual_library brief (``mos-visual-request-v1`` with ``source: library``) becomes a creative-request-v1
with origin ``marketing-os-library`` and no campaign copy; its treatment is the brief's own direction; its copy-free
export is accepted only for such requests; its handoff carries the library target. Fixture business only.
"""
from __future__ import annotations

import copy
import io
import json
import unittest
import uuid

from service.brand.errors import InputRefusal
from service.brand.mos_brandkit import build_package, package_version_ref
from service.composition.canonical import artifact_sha256
from service.composition.contracts import errors as schema_errors
from service.composition.errors import CompositionError
from service.composition.handoff import build_handoff
from service.requests import documents, mos_intake
from service.runtime.gateway import HttpMarketingOsGateway
from tests.creative_studio import b2_mos_fixtures as fx
from tests.creative_studio.test_b4_export_handoff import CREATED, ExportAndHandoff

KEY = "lib-home-services-av-home-services-brand_scene-1x1"


def library_sources(biz, camp) -> dict:
    ws = biz.mos_ws
    library = fx._vref(ws, "LibraryPack", "home-services-library")
    return {
        "schema_version": "mos-library-sources-v1", "workspace_id": ws, "library": library,
        "library_approval": fx._sealed({"receipt_id": fx.uid("library-approval"), "authority": "marketing-os",
                                        "workspace_id": ws, "subject": library, "decision": "approved",
                                        "actor_id": fx.uid("user", "reviewer"), "actor_role": "reviewer",
                                        "approved_at": fx.APPROVED_AT, "policy_revision": "mos-library-review-v1",
                                        "sha256": "0" * 64}),
        "item": {"key": KEY, "offer_group": "home-services", "kind": "brand_scene", "canvas": "1:1", "production": "generate"},
        "concept_direction": camp.direction,
        "brand_kit": {"id": biz.artifact_id, "version": 1},
        "policy": fx._vref(ws, "GenerationPolicy", "library"),
        "offering": camp.offering, "audiences": [fx._vref(ws, "Avatar", "home-services")],
        "objective": camp.objective, "hook": "Brand scenes for the home services offer", "locale": "en-GB",
        "forbidden": ["No before-and-after comparisons."],
        "budget": {"currency": "USD", "max_cost_micros": 4_000_000, "funding_source": "workspace",
                   "policy": fx._vref(ws, "LibraryCap", "home-services"), "expires_at": "2026-12-31T00:00:00.000Z"},
        "provider_selection": None,
    }


def library_request(biz, **over) -> dict:
    return {"schema_version": "mos-visual-request-v1", "station": "library", "step": "library_prepare",
            "workspace_id": biz.mos_ws, "source": "library",
            "library": {"artifact_id": fx.uid("LibraryPack", "home-services-library"), "version": 1, "item_key": KEY,
                        "offer_group": "home-services", "kind": "brand_scene"},
            "placement_id": None, "concept_id": None, "tier": "draft", "media_kind": "image", "canvas": "1:1", "slot": 1,
            "reference": None, "copy_refs": [], "content_pack_ref": None, "production_script": None,
            "composition": {"exact_layers": "none", "text_model_drawn": False}, **over}


class LibraryIntake(unittest.TestCase):
    def setUp(self):
        self.biz, self.camp = fx.home_services()
        doc = build_package(self.biz.export()).document
        self.pkg = {"document": doc, "ref": package_version_ref(doc), "source_artifact_id": self.biz.artifact_id}
        self.binding = fx.uid("connection", "library")

    def fields(self, visual=None, sources=None):
        return mos_intake.build_library_request_fields(visual or library_request(self.biz),
                                                       sources or library_sources(self.biz, self.camp), self.pkg, self.binding)

    def refused(self, code, **kw):
        with self.assertRaises(InputRefusal) as ctx:
            self.fields(**kw)
        self.assertEqual(ctx.exception.code, code)

    def test_library_request_validates_without_copy(self):
        f = self.fields()
        self.assertEqual(f["origin"]["kind"], "marketing-os-library")
        self.assertEqual(f["origin"]["library_item_key"], KEY)
        self.assertEqual(f["exact_copy"], [])
        self.assertEqual(f["budget"]["credential_binding_id"], self.binding, "funded by the requesting connection")
        self.assertIn("No before-and-after comparisons.", f["constraints"]["forbidden"])
        src = library_sources(self.biz, self.camp)
        f["origin"]["generation_eligibility"] = fx._sealed({
            "id": fx.uid("eligibility", "library"), "authority": "marketing-os", "workspace_id": self.biz.mos_ws,
            "action": "generate", "decision": "allowed", "actor_id": fx.uid("user", "producer"),
            "source_versions": [src["library"]], "policy": src["policy"],
            "approval_receipt_ids": [src["library_approval"]["receipt_id"]], "checked_at": "2026-10-31T08:59:30Z",
            "valid_until": "2026-10-31T09:00:30Z", "reason_codes": [], "sha256": "0" * 64})
        ws, actor = fx.uid("studio-ws", "library"), fx.uid("user", "producer")
        req = documents.new_request(f, workspace_id=ws, actor_id=actor)          # validates creative-request-v1
        tr = documents.new_treatment(mos_intake.build_library_treatment_fields(src, documents.version_ref(req, "creative-request"),
                                                                               self.pkg["ref"]), workspace_id=ws, actor_id=actor)
        self.assertEqual(tr["direction"]["summary"], self.camp.direction["summary"])
        self.assertEqual(tr["sources"][0], src["library"])

    def test_refusals(self):
        src = library_sources(self.biz, self.camp)
        self.refused("source_unavailable", sources={**src, "item": {**src["item"], "production": "owner_photo"}})
        self.refused("source_revision_changed", visual=library_request(self.biz, library={"item_key": "lib-other"}))
        self.refused("unsupported_canvas", visual=library_request(self.biz, canvas="4:5"))
        self.refused("unsupported_canvas", visual=library_request(self.biz, tier="final"))
        self.refused("budget_missing", sources={k: v for k, v in src.items() if k != "budget"})
        self.refused("approval_stale", sources={**src, "brand_kit": {"id": fx.uid("other-brandkit"), "version": 1}})
        self.refused("scope_mismatch", visual=library_request(self.biz, workspace_id=fx.uid("other-ws")))
        self.assertTrue(mos_intake.is_library_request(library_request(self.biz)))
        self.assertFalse(mos_intake.is_library_request(self.camp.visual_request()))


class CampaignBudgetBinding(unittest.TestCase):
    def test_campaign_budget_without_binding_is_funded_by_the_connection(self):
        biz, camp = fx.home_services()
        doc = build_package(biz.export()).document
        pkg = {"document": doc, "ref": package_version_ref(doc), "source_artifact_id": biz.artifact_id}
        src = camp.sources()
        src["budget"] = {k: v for k, v in src["budget"].items() if k != "credential_binding_id"}
        binding = fx.uid("connection", "campaign")
        f = mos_intake.build_request_fields(camp.visual_request(), src, pkg, binding)
        self.assertEqual(f["budget"]["credential_binding_id"], binding)
        kept = mos_intake.build_request_fields(camp.visual_request(), camp.sources(), pkg, binding)
        self.assertEqual(kept["budget"]["credential_binding_id"], camp.sources()["budget"]["credential_binding_id"],
                         "an explicit MOS binding is kept")


class LibraryExportAndHandoff(ExportAndHandoff):
    """Reuses the CS-B4 world: a library export is the same manifest without copy; its handoff has the library target."""

    def library_target(self):
        return {"authority": "marketing-os", "workspace_id": self.w.target["workspace_id"],
                "library": self.w.target["concept"], "library_item_key": KEY, "policy": self.w.target["policy"]}

    def copy_free(self, export):
        e = copy.deepcopy(export)
        e["exact_copy"] = []
        e["sha256"] = artifact_sha256(e)
        return e

    def test_copy_free_export_is_v2_only_and_hands_off_to_the_library(self):
        e = self.copy_free(self.export("square"))
        self.assertEqual(schema_errors(e, "export-v2.schema.json"), [])
        self.assertNotEqual(schema_errors(dict(e, schema_version="cs-export-v1"), "export-v1.schema.json"), [])
        h = self.handoff([e], target=self.library_target())
        self.assertEqual(schema_errors(h, "mos-handoff-v1.schema.json"), [])
        self.assertEqual(h["target"]["library_item_key"], KEY)

    def test_library_handoff_refuses_campaign_copy(self):
        self.refuse("copy_revision_mismatch", lambda: self.handoff([self.export("square")], target=self.library_target()))


class GatewaySourcesCall(unittest.TestCase):
    def test_sources_callback_posts_the_visual_request(self):
        g = HttpMarketingOsGateway("https://mos.example.test", assertion_key="k" * 48)
        seen = {}

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_open(req, timeout):
            seen.update(method=req.get_method(), url=req.full_url, body=json.loads(req.data.decode()),
                        signed="X-CS-Service-Assertion" in req.headers or "X-cs-service-assertion" in req.headers)
            return Resp(json.dumps({"schema_version": "mos-library-sources-v1"}).encode())

        g._open = fake_open
        visual = {"source": "library", "workspace_id": fx.uid("mos")}
        out = g.campaign_sources(mos_workspace_id=fx.uid("mos"), visual_request=visual, user_id=fx.uid("user"))
        self.assertEqual(out["schema_version"], "mos-library-sources-v1")
        self.assertEqual(seen["method"], "POST")
        self.assertTrue(seen["url"].endswith(f"action=sources&workspace_id={fx.uid('mos')}"))
        self.assertEqual(seen["body"], {"visual_request": visual})
        self.assertTrue(seen["signed"])


if __name__ == "__main__":
    unittest.main()


class TextFreeCompositionNeedsNoFontTests(unittest.TestCase):
    """A library image (scene + logo, no copy) builds no text layer, so it must not need a font file at all:
    brands whose only fonts are web formats (WOFF2) still get library images (2026-11-05)."""

    def test_no_copy_builds_no_text_layers_without_fonts(self):
        from service.postrun import layout
        chosen, _ = layout.pick_copy([])
        self.assertFalse(any(chosen[k] for k in ("headline", "body", "cta")) or chosen["disclaimers"])
        layers = layout.build_layers(width=1080, height=1080, safe_rect=(60, 60, 960, 960),
                                     scene={"asset_version_id": "s", "sha256": "0" * 64}, logo={"asset_version_id": "l", "sha256": "1" * 64},
                                     logo_id="logo-primary", logo_px=(400, 100), clear_space_ratio=0.25,
                                     fonts={}, weights={}, chosen=chosen, palette=[], step=1.0)
        self.assertFalse([l for l in layers if l.get("type") == "text"])
        self.assertFalse([l for l in layers if l.get("id") == "scrim"], "no text, no legibility scrim")
        self.assertTrue([l for l in layers if l.get("type") == "image"])

    def test_runtime_loads_fonts_only_when_there_is_text(self):
        import inspect
        from service.postrun import runtime
        src = inspect.getsource(runtime)
        self.assertIn("self._fonts(ctx, source) if has_text else ({}, {})", src)
