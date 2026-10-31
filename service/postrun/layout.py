"""Deterministic canvas layout for the post-run composition (CS-17b).

Pure function of (canvas size, generated image, exact brand logo/fonts, exact copy, safe zone, palette): same inputs
give the same composition document and therefore (via the B4 renderer) identical bytes. Text is never drawn by a model:
every string is an exact-copy text layer (`copy_id` bound) rendered by composition from the brand font file.

Interpretations recorded in the composition receipt / state detail (the contract leaves them open):
  * one headline, the first body entry and the first CTA are laid out; every `disclaimer` entry is laid out (regulated
    wording is never dropped); other roles / extra body tiers are NOT rendered and are listed in `copy_not_rendered`.
  * type scale shrinks in fixed steps (no auto-shrink inside the renderer); if the lowest step still overflows the
    result is the typed refusal `text_overflow`.
"""
from __future__ import annotations

import re
import uuid

from ..composition.safe_zones import SafeZone

LAYOUT_VERSION = "cs-17b-layout-v1"
_NS = uuid.UUID("5c5a5b0e-17b0-4d3e-9c4a-0c5e17b0a001")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

MARGIN_RATIO = 0.06
SCRIM_START_RATIO = 0.5          # scrim begins this fraction down the artboard (clamped above the text block)
SCRIM_FILL = "#000000B3"
TEXT_FILL = "#FFFFFF"
DARK_FILL = "#101010"
BOARD_BACKGROUND = "#000000"
HEADLINE_SIZE_RATIO, BODY_SIZE_RATIO, DISCLAIMER_SIZE_RATIO = 0.07, 0.034, 0.018
HEADLINE_BOX_RATIO, BODY_BOX_RATIO, CTA_BOX_RATIO, DISCLAIMER_BOX_RATIO = 0.17, 0.11, 0.07, 0.04
GAP_RATIO = 0.02
LOGO_WIDTH_RATIO = 0.2
CTA_WIDTH_RATIO = 0.5
LADDER = (1.0, 0.85, 0.7, 0.55)
LINE_HEIGHT = 1.15

# marketing-os registry/channel-specs.yaml@9a9caa07 meta_ads image 9:16 safe_zone (same citation as the B4 fixtures).
STORY_SAFE = dict(top_pct=14, right_pct=0, bottom_pct=14, left_pct=0,
                  source="marketing-os registry/channel-specs.yaml@9a9caa07 meta_ads image 9:16 safe_zone")


def safe_zone_for(preset: str, width: int, height: int) -> SafeZone:
    if preset == "story_1080x1920":
        return SafeZone.from_percent(width, height, **STORY_SAFE)
    return SafeZone.artboard_bounds()          # explicit recorded choice: no channel safe zone cited for this canvas


def u5(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "|".join(parts)))


def pick_copy(exact_copy: list[dict]) -> tuple[dict, list[str]]:
    """Which exact-copy entries are laid out, and which are deliberately not."""
    first = {}
    for c in exact_copy:
        first.setdefault(c["role"], c)
    chosen = {"headline": first.get("headline"), "body": first.get("body"), "cta": first.get("cta"),
              "disclaimers": [c for c in exact_copy if c["role"] == "disclaimer"]}
    used = {c["id"] for k, v in chosen.items() for c in ([v] if isinstance(v, dict) else (v or [])) if c}
    return chosen, sorted(c["id"] for c in exact_copy if c["id"] not in used)


def _lum(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def cta_colors(palette: list[dict]) -> tuple[str, str]:
    """Button fill from the brand palette (first role mentioning accent / cta / call to action / secondary / primary),
    text colour by contrast; white button when the palette names none (explicit, recorded default)."""
    usable = [p for p in palette if _HEX.match(p.get("hex") or "")]
    fill = "#FFFFFF"
    for key in ("accent", "cta", "call to action", "secondary", "primary"):
        hit = next((p for p in usable if key in (p.get("role") or "").lower()), None)
        if hit:
            fill = hit["hex"]
            break
    return fill.upper(), (DARK_FILL if _lum(fill) > 0.5 else TEXT_FILL)


def _t(x, y, w, h):
    return {"x": int(x), "y": int(y), "width": int(w), "height": int(h), "rotation_deg": 0, "opacity": 1}


def _text(lid, name, order, box, copy, font, size, weight, fill, align, valign):
    return {"id": lid, "name": name, "type": "text", "transform": _t(*box), "order": order, "locked": False,
            "content": {"text": copy["text"], "copy_id": copy["id"], "font": font,
                        "style": {"font_size_px": int(size), "weight": weight, "line_height": LINE_HEIGHT,
                                  "letter_spacing_px": 0, "fill": fill, "align": align, "vertical_align": valign}}}


def build_layers(*, width: int, height: int, safe_rect: tuple, scene: dict, logo: dict, logo_id: str,
                 logo_px: tuple[int, int], clear_space_ratio: float, fonts: dict, weights: dict, chosen: dict,
                 palette: list[dict], step: float) -> list[dict]:
    """fonts/weights: {'heading': ref, 'body': ref}. step: scale applied to the text sizes."""
    x0, y0, x1, y1 = safe_rect
    m = round(width * MARGIN_RATIO)
    left, cw = x0 + m, (x1 - x0) - 2 * m
    gap = round(height * GAP_RATIO)
    layers: list[dict] = []
    order = [0]

    def add(layer):
        layer["order"] = order[0]
        order[0] += 1
        layers.append(layer)

    add({"id": "scene", "name": "Generated scene", "type": "image", "transform": _t(0, 0, width, height), "order": 0,
         "locked": True, "content": {"asset": scene, "fit": "cover", "crop": None}})
    # stack from the bottom: disclaimers, cta, body, headline
    y = y1 - m
    texts = []
    for i, d in reversed(list(enumerate(chosen["disclaimers"], 1))):
        h = round(height * DISCLAIMER_BOX_RATIO)
        y -= h
        texts.append(("disclaimer-%d" % i, f"Disclaimer {i}", (left, y, cw, h), d, round(width * DISCLAIMER_SIZE_RATIO),
                      fonts["body"], weights["body"], TEXT_FILL, "left", "bottom"))
        y -= gap // 2
    cta_box = None
    if chosen["cta"]:
        ch = round(height * CTA_BOX_RATIO)
        y -= ch
        cta_box = (left, y, min(cw, round(width * CTA_WIDTH_RATIO)), ch)
        y -= gap
    if chosen["body"]:
        bh = round(height * BODY_BOX_RATIO)
        y -= bh
        texts.append(("body", "Body", (left, y, cw, bh), chosen["body"], round(width * BODY_SIZE_RATIO * step),
                      fonts["body"], weights["body"], TEXT_FILL, "left", "top"))
        y -= gap
    if chosen["headline"]:
        hh = round(height * HEADLINE_BOX_RATIO)
        y -= hh
        texts.append(("headline", "Headline", (left, y, cw, hh), chosen["headline"],
                      round(width * HEADLINE_SIZE_RATIO * step), fonts["heading"], weights["heading"], TEXT_FILL,
                      "left", "bottom"))
    text_top = y
    scrim_top = max(0, min(round(height * SCRIM_START_RATIO), text_top - m))
    add({"id": "scrim", "name": "Scrim", "type": "shape", "transform": _t(0, scrim_top, width, height - scrim_top),
         "order": 0, "locked": False,
         "content": {"shape": "rectangle", "fill": SCRIM_FILL, "stroke": None, "stroke_width_px": 0,
                     "corner_radius_px": 0}})
    # logo: top-left inside the safe zone, outside its own clear space
    lw = round(width * LOGO_WIDTH_RATIO)
    lh = max(1, round(lw * logo_px[1] / logo_px[0]))
    pad = max(m, int(clear_space_ratio * min(lw, lh)) + 1)
    add({"id": "logo", "name": "Logo", "type": "logo", "transform": _t(x0 + pad, y0 + pad, lw, lh), "order": 0,
         "locked": False, "content": {"asset": logo, "brand_logo_id": logo_id, "fit": "contain"}})
    for lid, name, box, copy, size, font, weight, fill, align, valign in sorted(texts, key=lambda t: t[2][1]):
        add(_text(lid, name, 0, box, copy, font, size, weight, fill, align, valign))
    if cta_box is not None:
        bg, fg = cta_colors(palette)
        add({"id": "cta-bg", "name": "CTA button", "type": "shape", "transform": _t(*cta_box), "order": 0,
             "locked": False,
             "content": {"shape": "rectangle", "fill": bg, "stroke": None, "stroke_width_px": 0,
                         "corner_radius_px": cta_box[3] // 2}})
        add(_text("cta", "CTA", 0, cta_box, chosen["cta"], fonts["heading"], round(cta_box[3] * 0.42 * min(step, 1.0)),
                  weights["heading"], fg, "center", "center"))
    return layers


def composition_doc(*, workspace_id: str, run_id: str, canvas: str, created_at: str, created_by: str, recipe: dict,
                    artboard_id: str, width: int, height: int, layers: list[dict]) -> dict:
    return {"schema_version": "cs-composition-v1", "id": u5("composition", run_id, canvas),
            "version_id": u5("composition-v", run_id, canvas, LAYOUT_VERSION), "revision": 1,
            "workspace_id": workspace_id, "created_at": created_at, "created_by": created_by, "sha256": "",
            "kind": "image", "basis_recipe": recipe, "basis_run": None,
            "artboards": [{"id": artboard_id, "width": width, "height": height, "color_space": "srgb",
                           "background": BOARD_BACKGROUND, "layers": layers}],
            "video": None}
