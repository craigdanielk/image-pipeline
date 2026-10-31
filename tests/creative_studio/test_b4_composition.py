"""CS-B4 exact image composition: golden images + typed refusals.

Accept: golden-image tests for 1:1 / 4:5 / 9:16 / 16:9 with exact logo + text;
renderer refuses missing font/logo with a typed error.

Goldens: b4_goldens/<artboard>.png + goldens.json (renderer version, sha256).
Regenerate deliberately with CS_B4_REGEN_GOLDENS=1 (a renderer-version change
is a visible diff, never a silent skip).
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import unittest

from PIL import Image, ImageChops

from service.composition.canonical import artifact_sha256, canonical_bytes, strict_loads, CanonicalizationError
from service.composition.errors import CompositionError
from service.composition.renderer import render_artboard, renderer_version
from service.composition.safe_zones import SafeZone
from tests.creative_studio.b4_support import GOLDENS, World, asset_bytes

CANVASES = {"square": (1080, 1080), "portrait": (1080, 1350), "story": (1080, 1920), "landscape": (1920, 1080)}


class GoldenImages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = World()
        cls.results = {b: render_artboard(cls.w.composition, b, brand_package=cls.w.package, assets=cls.w.source,
                                          safe_zone=cls.w.zone(b)) for b in CANVASES}
        manifest_path = GOLDENS / "goldens.json"
        if os.environ.get("CS_B4_REGEN_GOLDENS") == "1":
            GOLDENS.mkdir(exist_ok=True)
            for b, r in cls.results.items():
                (GOLDENS / f"{b}.png").write_bytes(r.data)
            manifest_path.write_text(json.dumps({"renderer_version": renderer_version(), "composition_sha256":
                                                 cls.w.composition["sha256"], "artboards": {
                                                     b: {"sha256": r.sha256, "width": r.width, "height": r.height}
                                                     for b, r in cls.results.items()}}, indent=2) + "\n")
        cls.golden = json.loads(manifest_path.read_text())

    def test_01_renderer_version_matches_goldens(self):
        self.assertEqual(self.golden["renderer_version"], renderer_version(),
                         "renderer stack changed; regenerate goldens deliberately and review the diff")
        self.assertEqual(self.golden["composition_sha256"], self.w.composition["sha256"])

    def test_02_each_canvas_matches_golden_bytes(self):
        for board, (width, height) in CANVASES.items():
            with self.subTest(board=board):
                r = self.results[board]
                golden = (GOLDENS / f"{board}.png").read_bytes()
                self.assertEqual((r.width, r.height), (width, height))
                self.assertEqual(r.sha256, self.golden["artboards"][board]["sha256"])
                self.assertEqual(r.data, golden, "rendered bytes differ from golden")
                self.assertEqual(hashlib.sha256(golden).hexdigest(), r.sha256)

    def test_03_same_input_identical_bytes(self):
        for board in CANVASES:
            again = render_artboard(copy.deepcopy(self.w.composition), board, brand_package=self.w.package,
                                    assets=self.w.source, safe_zone=self.w.zone(board))
            self.assertEqual(again.data, self.results[board].data)
            self.assertEqual(again.receipt, self.results[board].receipt)

    def test_04_receipt_records_sources_fonts_copy_and_version(self):
        for board, r in self.results.items():
            rc = r.receipt
            self.assertEqual(rc["renderer_version"], renderer_version())
            self.assertEqual(rc["output"]["sha256"], r.sha256)
            roles = {(s["role"], s["asset"]["sha256"]) for s in rc["sources"]}
            self.assertIn(("logo", hashlib.sha256(asset_bytes("logo")).hexdigest()), roles)
            self.assertIn(("image", hashlib.sha256(asset_bytes("scene")).hexdigest()), roles)
            self.assertEqual([f["sha256"] for f in rc["fonts"]], [hashlib.sha256(asset_bytes("font")).hexdigest()])
            self.assertEqual(rc["fonts"][0]["weight"], 400)
            self.assertEqual({c["copy_id"]: c["text"] for c in rc["exact_copy"]},
                             {"headline": "Fast, friendly home repairs", "cta": "Book a visit today"})
            for c in rc["exact_copy"]:
                self.assertEqual(c["text_sha256"], hashlib.sha256(c["text"].encode()).hexdigest())
            self.assertEqual(rc["safe_zone"]["source"], self.w.zone(board).source)
        # 9:16 uses the cited 14%/14% channel zone: ceil(1920*0.14) = 269 px top and bottom.
        self.assertEqual(self.results["story"].receipt["safe_zone"]["usable_rect"], [0, 269, 1080, 1651])

    def test_05_logo_pixels_are_the_exact_logo_file(self):
        """Native-size logo: output region == logo file alpha-composited over the logo-less render."""
        def place(doc, with_logo):
            board = next(b for b in doc["artboards"] if b["id"] == "square")
            logo = next(l for l in board["layers"] if l["id"] == "logo")
            logo["transform"].update({"x": 60, "y": 60, "width": 480, "height": 192})
            if not with_logo:
                board["layers"] = [l for l in board["layers"] if l["id"] != "logo"]
            head = next(l for l in board["layers"] if l["id"] == "headline")
            head["transform"].update({"y": 700})
        with_logo = render_artboard(self.w.edited(lambda d: place(d, True)), "square", brand_package=self.w.package,
                                    assets=self.w.source, safe_zone=SafeZone.artboard_bounds())
        without = render_artboard(self.w.edited(lambda d: place(d, False)), "square", brand_package=self.w.package,
                                  assets=self.w.source, safe_zone=SafeZone.artboard_bounds())
        base = Image.open(io.BytesIO(without.data)).convert("RGBA")
        logo = Image.open(io.BytesIO(asset_bytes("logo"))).convert("RGBA")
        expected = base.copy()
        expected.alpha_composite(logo, (60, 60))
        got = Image.open(io.BytesIO(with_logo.data)).convert("RGBA")
        self.assertIsNone(ImageChops.difference(got, expected).getbbox(), "logo pixels are not the exact file")

    def test_06_text_is_rendered_and_changes_with_copy(self):
        changed = self.w.edited(lambda d: self.w.layer(d, "square", "headline")["content"].update(
            text="Fast, friendly home repair"))
        r = render_artboard(changed, "square", brand_package=self.w.package, assets=self.w.source,
                            safe_zone=SafeZone.artboard_bounds())
        diff = ImageChops.difference(Image.open(io.BytesIO(r.data)),
                                     Image.open(io.BytesIO(self.results["square"].data))).getbbox()
        self.assertIsNotNone(diff)
        x0, y0, x1, y1 = diff
        self.assertTrue(60 <= x0 and x1 <= 1020 and 640 <= y0 and y1 <= 840, diff)  # inside headline box only

    def test_07_wraps_deterministically_and_keeps_newlines(self):
        def narrow(d):
            h = self.w.layer(d, "square", "headline")
            h["transform"].update({"width": 520, "height": 260})
            h["content"]["text"] = "Fast, friendly\nhome repairs nearby"
        r = render_artboard(self.w.edited(narrow), "square", brand_package=self.w.package, assets=self.w.source,
                            safe_zone=SafeZone.artboard_bounds())
        lines = next(t for t in r.receipt["text_layout"] if t["layer_id"] == "headline")["lines"]
        self.assertEqual(lines, ["Fast, friendly", "home repairs", "nearby"])

    def test_08_jpeg_and_webp_are_deterministic(self):
        for mime in ("image/jpeg", "image/webp"):
            a = render_artboard(self.w.composition, "square", brand_package=self.w.package, assets=self.w.source,
                                safe_zone=SafeZone.artboard_bounds(), mime_type=mime)
            b = render_artboard(self.w.composition, "square", brand_package=self.w.package, assets=self.w.source,
                                safe_zone=SafeZone.artboard_bounds(), mime_type=mime)
            self.assertEqual(a.data, b.data)
            self.assertEqual(Image.open(io.BytesIO(a.data)).format, {"image/jpeg": "JPEG", "image/webp": "WEBP"}[mime])


class TypedRefusals(unittest.TestCase):
    def setUp(self):
        self.w = World()

    def refuse(self, code, comp=None, *, board="square", package=None, source=None, zone=None, **kw):
        with self.assertRaises(CompositionError) as cm:
            render_artboard(comp or self.w.composition, board, brand_package=package or self.w.package,
                            assets=source or self.w.source, safe_zone=zone or SafeZone.artboard_bounds(), **kw)
        self.assertEqual(cm.exception.code, code, cm.exception.message)
        err = cm.exception.to_error()
        self.assertEqual(set(err), {"code", "message", "retryable", "field_paths", "related_ids"})
        return cm.exception

    def _fresh_source(self, skip):
        from service.composition.assets import MemoryAssetSource
        src = MemoryAssetSource()
        for key in ("logo", "font", "scene"):
            if key != skip:
                src.put(self.w.records[key]["ref"], asset_bytes(key))
        return src

    # -- font --
    def test_font_bytes_missing(self):
        e = self.refuse("font_asset_missing", source=self._fresh_source("font"))
        self.assertIn("/content/font", e.field_paths[0])

    def test_font_not_in_brand_package(self):
        pkg = copy.deepcopy(self.w.package)
        pkg["typography"][0]["font_assets"][0]["sha256"] = "0" * 64
        pkg["sha256"] = artifact_sha256(pkg)
        self.refuse("font_asset_missing", package=pkg)

    def test_font_weight_missing_no_synthetic_bold(self):
        comp = self.w.edited(lambda d: self.w.layer(d, "square", "headline")["content"]["style"].update(weight=700))
        self.refuse("font_weight_missing", comp)

    def test_glyph_missing_no_substitution(self):
        comp = self.w.edited(lambda d: self.w.layer(d, "square", "headline")["content"].update(text="Café repairs"))
        e = self.refuse("glyph_missing", comp)
        self.assertIn("U+00E9", e.message)

    def test_font_bytes_tampered(self):
        from service.composition.assets import MemoryAssetSource
        src = self._fresh_source("font")
        src.put(self.w.records["font"]["ref"], asset_bytes("font") + b"\0")
        self.refuse("asset_hash_mismatch", source=src)

    # -- logo --
    def test_logo_bytes_missing(self):
        self.refuse("logo_asset_missing", source=self._fresh_source("logo"))

    def test_logo_not_in_package(self):
        comp = self.w.edited(lambda d: self.w.layer(d, "square", "logo")["content"].update(brand_logo_id="reversed"))
        self.refuse("logo_asset_missing", comp)

    def test_logo_layer_with_other_bytes(self):
        scene = self.w.records["scene"]["ref"]
        comp = self.w.edited(lambda d: self.w.layer(d, "square", "logo")["content"].update(asset=scene))
        self.refuse("logo_not_from_brand_package", comp)

    def test_logo_below_min_width(self):
        comp = self.w.edited(lambda d: self.w.layer(d, "square", "logo")["transform"].update(width=150, height=60))
        self.refuse("logo_below_min_width", comp)

    def test_logo_rotation_refused(self):
        comp = self.w.edited(lambda d: self.w.layer(d, "square", "logo")["transform"].update(rotation_deg=15))
        self.refuse("logo_transform_unsupported", comp)

    def test_logo_clear_space(self):
        comp = self.w.edited(lambda d: self.w.layer(d, "square", "headline")["transform"].update(y=190))
        self.refuse("logo_clear_space_violation", comp)
        edge = self.w.edited(lambda d: self.w.layer(d, "square", "logo")["transform"].update(x=10, y=10))
        self.refuse("logo_clear_space_violation", edge)

    # -- text / placement --
    def test_text_overflow_no_truncation(self):
        comp = self.w.edited(lambda d: self.w.layer(d, "square", "headline")["transform"].update(height=60))
        self.refuse("text_overflow", comp)
        long_word = self.w.edited(lambda d: self.w.layer(d, "square", "headline")["content"].update(
            text="Supercalifragilisticexpialidocious-repairs"))
        self.refuse("text_overflow", long_word)

    def test_outside_safe_zone(self):
        comp = self.w.edited(lambda d: self.w.layer(d, "story", "logo")["transform"].update(y=100))
        self.refuse("outside_safe_zone", comp, board="story", zone=self.w.zone("story"))

    def test_out_of_artboard(self):
        comp = self.w.edited(lambda d: self.w.layer(d, "square", "headline")["transform"].update(x=300))
        self.refuse("layer_out_of_bounds", comp)

    # -- integrity / scope --
    def test_scene_bytes_tampered(self):
        src = self._fresh_source("scene")
        src.put(self.w.records["scene"]["ref"], asset_bytes("scene")[:-1])
        self.refuse("asset_hash_mismatch", source=src)

    def test_cross_workspace_asset(self):
        def other(d):
            self.w.layer(d, "square", "scene")["content"]["asset"]["workspace_id"] = self.w.ids["mos_workspace"]
        self.refuse("scope_mismatch", self.w.edited(other))

    def test_edited_without_rehash(self):
        comp = copy.deepcopy(self.w.composition)
        self.w.layer(comp, "square", "headline")["content"]["text"] = "Changed"
        self.refuse("artifact_hash_mismatch", comp)

    def test_schema_violation_has_pointer(self):
        comp = self.w.edited(lambda d: self.w.layer(d, "square", "headline").update(unexpected=True))
        e = self.refuse("invalid_composition", comp)
        self.assertTrue(any(p.startswith("/artboards/0/layers/3") for p in e.field_paths), e.field_paths)

    def test_group_cycle_and_order(self):
        def cyc(d):
            board = self.w.board("square", d)
            board["layers"].append({"id": "g2", "name": "g2", "type": "group", "transform": board["layers"][4][
                "transform"], "order": 9, "locked": False, "content": {"child_ids": ["cta-group"]}})
            self.w.layer(d, "square", "cta-text")["content"] = self.w.layer(d, "square", "cta-text")["content"]
            self.w.layer(d, "square", "cta-group")["content"]["child_ids"].append("g2")
        self.refuse("invalid_composition", self.w.edited(cyc))
        dup = self.w.edited(lambda d: self.w.layer(d, "square", "headline").update(order=2))
        self.refuse("invalid_composition", dup)

    def test_crop_outside_source(self):
        comp = self.w.edited(lambda d: self.w.layer(d, "square", "scene")["content"].update(
            crop={"x": 0.5, "y": 0, "width": 0.6, "height": 1}))
        self.refuse("invalid_composition", comp)

    def test_unapproved_or_blocked_package(self):
        pkg = copy.deepcopy(self.w.package)
        pkg["source"]["approval_receipt_id"] = self.w.ids["actor"]
        pkg["sha256"] = artifact_sha256(pkg)
        self.refuse("brand_package_not_approved", package=pkg)
        pkg = copy.deepcopy(self.w.package)
        pkg["unresolved_decisions"] = [{"id": "logo-colour", "description": "Pending", "blocks_compile": True,
                                        "affected_asset_ids": [pkg["logos"][0]["asset"]["asset_id"]]}]
        pkg["sha256"] = artifact_sha256(pkg)
        self.refuse("brand_decision_unresolved", package=pkg)

    def test_jpeg_needs_opaque_background(self):
        comp = self.w.edited(lambda d: self.w.board("square", d).update(background="#0B1F3300"))
        self.refuse("jpeg_requires_opaque_background", comp, mime_type="image/jpeg")

    def test_safe_zone_is_explicit(self):
        with self.assertRaises(CompositionError) as cm:
            render_artboard(self.w.composition, "square", brand_package=self.w.package, assets=self.w.source,
                            safe_zone=None)
        self.assertEqual(cm.exception.code, "safe_zone_missing")


class Canonical(unittest.TestCase):
    def test_rfc8785_vectors(self):
        self.assertEqual(canonical_bytes({"b": [1e21, 1e-7, 0.000001, 333333333.33333329, -0.0], "a": "€\n"}),
                         '{"a":"€\\n","b":[1e+21,1e-7,0.000001,333333333.3333333,0]}'.encode())

    def test_strict_parse(self):
        with self.assertRaises(CanonicalizationError):
            strict_loads('{"a":1,"a":2}')
        with self.assertRaises(CanonicalizationError):
            strict_loads('{"a":NaN}')
        with self.assertRaises(CanonicalizationError):
            canonical_bytes({"a": 2 ** 53})


if __name__ == "__main__":
    unittest.main()
