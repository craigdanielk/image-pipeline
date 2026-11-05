"""Task 3: the compositor stacks text in the shot's text zone; scrim only behind the text block."""
from __future__ import annotations

import unittest

from service.postrun import layout
from tests.creative_studio.b4_support import World
from tests.creative_studio.test_cs17b_unit import CANVASES, COPY

W = None


def layers_for(name, zone, copy_entries=None, logo_px=(480, 192)):
    global W
    W = W or World()
    preset, width, height = CANVASES[name]
    rec = W.records
    chosen, _ = layout.pick_copy(copy_entries or COPY)
    sz = layout.safe_zone_for(preset, width, height)
    rect = sz.rect(width, height)
    fonts = {"heading": rec["font"]["ref"], "body": rec["font"]["ref"]}
    out = layout.build_layers(
        width=width, height=height, safe_rect=rect, scene=rec["scene"]["ref"], logo=rec["logo"]["ref"],
        logo_id="primary", logo_px=logo_px, clear_space_ratio=0.5, fonts=fonts,
        weights={"heading": 400, "body": 400}, chosen=chosen, palette=W.package["palette"], step=1.0, text_zone=zone)
    return {l["id"]: l for l in out}, out, rect, (width, height)


def box(layer):
    t = layer["transform"]
    return t["x"], t["y"], t["width"], t["height"]


class TextZones(unittest.TestCase):
    def test_top_left_zone_stacks_from_top_inside_safe_zone(self):
        by, _, (x0, y0, x1, y1), (w, h) = layers_for("square", "top_left")
        self.assertGreaterEqual(box(by["headline"])[1], y0)
        self.assertGreater(box(by["cta"])[1], box(by["body"])[1])
        self.assertGreater(box(by["body"])[1], box(by["headline"])[1])
        self.assertGreater(box(by["disclaimer-1"])[1], box(by["cta"])[1])
        for lid in ("headline", "body", "cta", "disclaimer-1"):
            bx, by_, bw, bh = box(by[lid])
            self.assertLessEqual(bx + bw, round(w * 0.6))
            self.assertLessEqual(by_ + bh, y1)
        self.assertEqual(by["headline"]["content"]["style"]["align"], "left")

    def test_top_zone_uses_full_content_width(self):
        by, _, (x0, _, x1, _), (w, _) = layers_for("square", "top")
        self.assertEqual(box(by["headline"])[2], (x1 - x0) - 2 * round(w * layout.MARGIN_RATIO))

    def test_story_9x16_top_zone_respects_14pct_top_safe_zone(self):
        by, out, rect, (w, h) = layers_for("story", "top")
        self.assertEqual(rect[1], 269)
        for l in out:
            if l["id"] in ("scene",):
                continue
            self.assertGreaterEqual(box(l)[1], 269, l["id"])
            self.assertLessEqual(box(l)[1] + box(l)[3], h - 269, l["id"])

    def test_left_zone_is_vertically_centred_in_left_55pct(self):
        by, _, (x0, y0, x1, y1), (w, h) = layers_for("square", "left")
        top = box(by["headline"])[1]
        last = box(by["disclaimer-1"])
        bottom = last[1] + last[3]
        self.assertLessEqual(abs((top - y0) - (y1 - bottom)), 2)
        for lid in ("headline", "body", "cta", "disclaimer-1"):
            self.assertLessEqual(box(by[lid])[0] + box(by[lid])[2], round(w * 0.55))

    def test_scrim_only_behind_text_block(self):
        for zone in ("top_left", "top", "left"):
            by, _, _, (w, h) = layers_for("square", zone)
            sx, sy, sw, sh = box(by["scrim"])
            text = [box(by[i]) for i in ("headline", "body", "cta", "disclaimer-1")]
            self.assertLessEqual(sy, min(t[1] for t in text))
            self.assertGreaterEqual(sy + sh, max(t[1] + t[3] for t in text))
            if zone != "left":
                self.assertLess(sy + sh, h, "scrim must not run to the canvas bottom")
            self.assertLess(sx + sw, w, "scrim must not span the full width in a column zone") if zone != "top" else None
            for t in text:
                self.assertGreaterEqual(t[0], sx)
                self.assertLessEqual(t[0] + t[2], sx + sw)

    def test_logo_does_not_overlap_text_block(self):
        for zone in ("top_left", "top", "left"):
            for name in CANVASES:
                by, _, rect, _ = layers_for(name, zone)
                lx, ly, lw, lh = box(by["logo"])
                sx, sy, sw, sh = box(by["scrim"])
                self.assertFalse(lx < sx + sw and sx < lx + lw and ly < sy + sh and sy < ly + lh, (zone, name))
                self.assertGreaterEqual(lx, rect[0])
                self.assertLessEqual(lx + lw, rect[2])
                self.assertGreaterEqual(ly, rect[1])
                self.assertLessEqual(ly + lh, rect[3])

    def test_top_zone_moves_logo_to_bottom_corner(self):
        by, _, rect, _ = layers_for("square", "top")
        self.assertGreater(box(by["logo"])[1], rect[3] // 2)

    def test_none_zone_lays_out_no_text_only_scene_and_logo(self):
        by, out, _, _ = layers_for("square", "none")
        self.assertEqual([l["id"] for l in out], ["scene", "logo"])

    def test_no_logo_file_composes_without_logo_layer(self):
        for zone in ("bottom", "top", "left", "none"):
            preset, width, height = CANVASES["square"]
            rec = World().records
            chosen, _ = layout.pick_copy(COPY)
            rect = layout.safe_zone_for(preset, width, height).rect(width, height)
            out = layout.build_layers(
                width=width, height=height, safe_rect=rect, scene=rec["scene"]["ref"], logo=None, logo_id="primary",
                logo_px=None, clear_space_ratio=0.5, fonts={"heading": rec["font"]["ref"], "body": rec["font"]["ref"]},
                weights={"heading": 400, "body": 400}, chosen=chosen, palette=[], step=1.0, text_zone=zone)
            self.assertNotIn("logo", [l["id"] for l in out], zone)
            self.assertEqual(out[0]["id"], "scene")

    def test_default_bottom_is_unchanged(self):
        a = layers_for("square", "bottom")[1]
        W2 = World()
        preset, width, height = CANVASES["square"]
        rec = W2.records
        chosen, _ = layout.pick_copy(COPY)
        rect = layout.safe_zone_for(preset, width, height).rect(width, height)
        legacy = layout.build_layers(
            width=width, height=height, safe_rect=rect, scene=rec["scene"]["ref"], logo=rec["logo"]["ref"],
            logo_id="primary", logo_px=(480, 192), clear_space_ratio=0.5,
            fonts={"heading": rec["font"]["ref"], "body": rec["font"]["ref"]}, weights={"heading": 400, "body": 400},
            chosen=chosen, palette=W2.package["palette"], step=1.0)
        self.assertEqual(a, legacy)

    def test_unknown_zone_is_refused(self):
        with self.assertRaises(ValueError):
            layers_for("square", "diagonal")


if __name__ == "__main__":
    unittest.main()
