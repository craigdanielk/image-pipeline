"""CS-17b pure tests: the post-run composition is deterministic (golden image), inside every canvas's safe zone, and
exports on all four presets; text layers are exact-copy bound (never model drawn)."""
from __future__ import annotations

import copy
import io
import os
import unittest
from pathlib import Path

from PIL import Image

from service.composition.canonical import artifact_sha256
from service.composition.errors import CompositionError
from service.composition.export import IMAGE_PRESETS, build_export, check_format, MemoryExportStore
from service.composition.renderer import render_artboard
from service.composition.validate import validate_composition
from service.postrun import layout
from tests.creative_studio.b4_support import World, asset_bytes

GOLD = Path(__file__).parent / "b17b_goldens"
COPY = [{"id": "headline", "role": "headline", "text": "Fast, friendly home repairs", "language": "en-GB",
         "source_copy": None, "claim_refs": []},
        {"id": "body", "role": "body", "text": "Same-week visits from a vetted local technician.", "language": "en-GB",
         "source_copy": None, "claim_refs": []},
        {"id": "cta", "role": "cta", "text": "Book a visit today", "language": "en-GB", "source_copy": None,
         "claim_refs": []},
        {"id": "terms", "role": "disclaimer", "text": "Prices from 49 pounds. Terms apply.", "language": "en-GB",
         "source_copy": None, "claim_refs": []},
        {"id": "extra-body", "role": "body", "text": "A second body tier that is not laid out.", "language": "en-GB",
         "source_copy": None, "claim_refs": []}]
CANVASES = {"square": ("square_1080", 1080, 1080), "portrait": ("portrait_1080x1350", 1080, 1350),
            "story": ("story_1080x1920", 1080, 1920), "landscape": ("landscape_1920x1080", 1920, 1080)}


def compose(w: World, name: str, step: float = 1.0, copy_entries=None):
    preset, width, height = CANVASES[name]
    rec = w.records
    fonts = {"heading": rec["font"]["ref"], "body": rec["font"]["ref"]}
    chosen, not_rendered = layout.pick_copy(copy_entries or COPY)
    zone = layout.safe_zone_for(preset, width, height)
    layers = layout.build_layers(
        width=width, height=height, safe_rect=zone.rect(width, height), scene=rec["scene"]["ref"],
        logo=rec["logo"]["ref"], logo_id="primary", logo_px=(480, 192), clear_space_ratio=0.5, fonts=fonts,
        weights={"heading": 400, "body": 400}, chosen=chosen, palette=w.package["palette"], step=step)
    doc = layout.composition_doc(
        workspace_id=w.ids["workspace_id"] if "workspace_id" in w.ids else rec["logo"]["ref"]["workspace_id"],
        run_id="11111111-1111-4111-8111-111111111111", canvas=name, created_at="2026-10-31T09:00:00Z",
        created_by="e0f38aea-37e2-56fa-b5b1-2f4bf8d75c7c", recipe=w.composition["basis_recipe"], artboard_id=name,
        width=width, height=height, layers=layers)
    doc["sha256"] = artifact_sha256(doc)
    validate_composition(doc)
    render = render_artboard(doc, name, brand_package=w.package, assets=w.source, safe_zone=zone)
    return doc, render, not_rendered, zone


class PostRunComposition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = World()

    def test_golden_and_determinism(self):
        doc, r1, _, _ = compose(self.w, "square")
        _, r2, _, _ = compose(self.w, "square")
        self.assertEqual(r1.data, r2.data, "same inputs => identical bytes")
        for name in ("story",):
            self.assertEqual(compose(self.w, name)[1].sha256, compose(self.w, name)[1].sha256)
        path = GOLD / "post-run-square.png"
        if os.environ.get("CS_B17B_REGEN_GOLDENS") == "1":
            GOLD.mkdir(exist_ok=True)
            path.write_bytes(r1.data)
        self.assertEqual(path.read_bytes(), r1.data, "golden post-run composition changed (regenerate deliberately)")
        with Image.open(io.BytesIO(r1.data)) as im:
            self.assertEqual(im.size, (1080, 1080))

    def test_text_is_exact_copy_bound_never_model_drawn(self):
        doc, render, not_rendered, _ = compose(self.w, "square")
        texts = [l for l in doc["artboards"][0]["layers"] if l["type"] == "text"]
        self.assertEqual(sorted(l["content"]["copy_id"] for l in texts),
                         sorted(["cta", "headline", "body", "terms"]))
        for l in texts:
            entry = next(c for c in COPY if c["id"] == l["content"]["copy_id"])
            self.assertEqual(l["content"]["text"], entry["text"])
        self.assertEqual(not_rendered, ["extra-body"], "unrendered copy is listed, never silently dropped")
        self.assertEqual(sorted(c["copy_id"] for c in render.receipt["exact_copy"]),
                         sorted(["cta", "headline", "body", "terms"]))
        # the provider image carries no text: it is a plain image layer under composed layers
        self.assertEqual(doc["artboards"][0]["layers"][0]["type"], "image")

    def test_every_preset_renders_inside_its_safe_zone_and_matches_export_presets(self):
        for name, (preset, width, height) in CANVASES.items():
            doc, render, _, zone = compose(self.w, name)
            self.assertEqual((render.width, render.height), (width, height))
            fmt = {"id": name, "kind": "image", "preset": preset, "width_px": width, "height_px": height,
                   "mime_type": "image/png", "duration_frames": None, "frame_rate": None, "count": 1,
                   "channel": "meta_ads"}
            check_format(fmt, render)
            x0, y0, x1, y1 = zone.rect(width, height)
            self.assertEqual(render.receipt["safe_zone"]["usable_rect"], [x0, y0, x1, y1])
            self.assertEqual(preset in IMAGE_PRESETS, True)
        story = compose(self.w, "story")[3]
        self.assertEqual((story.top, story.bottom), (269, 269), "9:16 uses the cited 14% safe zone")

    def test_overflow_is_a_typed_refusal_not_truncation(self):
        long = copy.deepcopy(COPY)
        long[0]["text"] = " ".join(["Extraordinarily"] * 40)
        with self.assertRaises(CompositionError) as c:
            compose(self.w, "square", copy_entries=long)
        self.assertEqual(c.exception.code, "text_overflow")

    def test_pick_copy_never_drops_a_disclaimer(self):
        chosen, rest = layout.pick_copy(COPY + [dict(COPY[3], id="terms-2", text="Second disclaimer")])
        self.assertEqual([d["id"] for d in chosen["disclaimers"]], ["terms", "terms-2"])
        self.assertEqual(rest, ["extra-body"])


if __name__ == "__main__":
    unittest.main()
