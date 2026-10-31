"""Deterministic exact image compositor (CS-B4 priority 1).

Input: one saved cs-composition-v1 artboard, the pinned brand-package-v1, an
AssetSource that returns exact bytes per AssetRef, and an explicit SafeZone.
Output: encoded bytes + sha256 + a render receipt listing every source hash,
font hash/weight/glyph coverage, exact copy text hashes, safe zone and the
renderer version. Same inputs + same renderer version => identical bytes.

Exactness rules (contract.md §5, §6, §10; COMPOSITION_MODEL.md):
* fonts come only from pinned font bytes listed in the brand package
  typography; weight must equal the font's OS/2 weight; every character must
  be in the font's cmap; system fonts / raqm shaping are never used;
* text is never truncated, shrunk or reflowed outside its saved box: overflow
  is a typed refusal;
* logos are the package's exact raster file, contain-fit, unrotated, full
  opacity, at least min_width_px wide, with clear space inside the artboard
  and free of other copy/logo layers;
* copy and logo layers must lie inside the artboard and the caller's safe zone.
"""
from __future__ import annotations

import hashlib
import io
import math
import warnings
from dataclasses import dataclass, field

import PIL
from PIL import Image, ImageDraw, ImageFont, features

from . import fontinfo
from .assets import AssetSource, load_exact, same_ref
from .canonical import text_sha256
from .errors import CompositionError
from .safe_zones import SafeZone
from .validate import semantic_sha256, validate_composition

RENDERER_ID = "cs-image-compositor"
RENDERER_REVISION = "1"
# contract.md §10: longest-edge 4096 is the input/normalization safety bound.
MAX_SOURCE_EDGE = 4096
_ENCODERS = {"image/png", "image/jpeg", "image/webp"}
_DECODED_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def renderer_version() -> str:
    """Everything that can change output bytes. Recorded in every receipt/export."""
    return (f"{RENDERER_ID}/{RENDERER_REVISION} pillow/{PIL.__version__} "
            f"freetype/{features.version('freetype2')} zlib/{features.version('zlib')} "
            f"jpeg/{features.version('libjpeg_turbo')} webp/{features.version('webp')} layout/basic")


@dataclass
class RenderResult:
    data: bytes
    sha256: str
    mime_type: str
    width: int
    height: int
    renderer_version: str
    receipt: dict = field(default_factory=dict)


def _r(value: float) -> int:
    """Round half up; never banker's rounding."""
    return math.floor(value + 0.5)


def _rgba(hex_color: str) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16) if len(h) == 8 else 255)


def _composite(canvas: Image.Image, surf: Image.Image, x: int, y: int) -> None:
    """alpha_composite with clipping for negative / overflowing offsets."""
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + surf.width, canvas.width), min(y + surf.height, canvas.height)
    if x0 >= x1 or y0 >= y1:
        return
    canvas.alpha_composite(surf.crop((x0 - x, y0 - y, x1 - x, y1 - y)), (x0, y0))


def _with_opacity(surf: Image.Image, opacity: float) -> Image.Image:
    if opacity >= 1:
        return surf
    alpha = surf.getchannel("A").point([_r(v * opacity) for v in range(256)])
    surf = surf.copy()
    surf.putalpha(alpha)
    return surf


def _rotate_point(px: float, py: float, cx: float, cy: float, deg: float) -> tuple[float, float]:
    rad = math.radians(deg)  # clockwise in screen coordinates (y down)
    dx, dy = px - cx, py - cy
    return (cx + dx * math.cos(rad) - dy * math.sin(rad), cy + dx * math.sin(rad) + dy * math.cos(rad))


class _Frame:
    """Absolute placement chain for a layer: origin offset + ancestor group rotations."""

    def __init__(self, ox: float = 0, oy: float = 0, rotations: tuple = ()):
        self.ox, self.oy, self.rotations = ox, oy, rotations

    def child(self, group: dict) -> "_Frame":
        t = group["transform"]
        gx, gy = self.ox + t["x"], self.oy + t["y"]
        rot = self.rotations
        if t["rotation_deg"]:
            rot = ((gx + t["width"] / 2, gy + t["height"] / 2, t["rotation_deg"]),) + rot
        return _Frame(gx, gy, rot)

    def aabb(self, t: dict) -> tuple[float, float, float, float]:
        x, y = self.ox + t["x"], self.oy + t["y"]
        cx, cy = x + t["width"] / 2, y + t["height"] / 2
        pts = [(x, y), (x + t["width"], y), (x, y + t["height"]), (x + t["width"], y + t["height"])]
        if t["rotation_deg"]:
            pts = [_rotate_point(px, py, cx, cy, t["rotation_deg"]) for px, py in pts]
        for gcx, gcy, deg in self.rotations:  # innermost first
            pts = [_rotate_point(px, py, gcx, gcy, deg) for px, py in pts]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))


class _Context:
    def __init__(self, composition, board, board_index, package, assets, safe_rect):
        self.composition = composition
        self.workspace_id = composition["workspace_id"]
        self.board = board
        self.base = f"/artboards/{board_index}/layers"
        self.index = {l["id"]: i for i, l in enumerate(board["layers"])}
        self.package = package
        self.assets = assets
        self.safe_rect = safe_rect
        self.sources: list[dict] = []
        self.fonts: dict[str, dict] = {}
        self.copy: list[dict] = []
        self.text_layout: list[dict] = []
        self.keepout: list[tuple[str, tuple[float, float, float, float]]] = []  # copy/logo boxes
        self.logo_clear: list[tuple[str, tuple[float, float, float, float]]] = []
        self._font_cache: dict[str, tuple[bytes, fontinfo.FontInfo]] = {}

    def path(self, layer: dict, suffix: str = "") -> str:
        return f"{self.base}/{self.index[layer['id']]}{suffix}"


# -- brand package checks -------------------------------------------------------
def _check_package(package: dict) -> None:
    from .contracts import require_valid
    require_valid(package, "brand-package-v1")
    if package["approval"]["receipt_id"] != package["source"]["approval_receipt_id"]:
        raise CompositionError("brand_package_not_approved",
                               "package approval receipt does not match its source approval receipt",
                               ["/approval/receipt_id"], [package["version_id"]])


def _package_asset(package: dict, sha256: str, mime: str):
    for record in package["assets"]:
        if record["ref"]["sha256"] == sha256 and record["ref"]["mime_type"] == mime:
            return record
    return None


def _blocking_decisions(package: dict, asset_ids: set[str]) -> list[str]:
    return [d["id"] for d in package["unresolved_decisions"]
            if d["blocks_compile"] and (not d["affected_asset_ids"] or asset_ids & set(d["affected_asset_ids"]))]


# -- layers -----------------------------------------------------------------------
def _decode_raster(data: bytes, ref: dict, role: str, path: str) -> Image.Image:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except (OSError, Image.DecompressionBombWarning, Image.DecompressionBombError):
            raise CompositionError(f"{role}_format_unsupported", f"{role} bytes are not a decodable PNG/JPEG/WebP "
                                   "raster (vector logos need an imported raster derivative)", [path],
                                   [ref["asset_version_id"]]) from None
    if _DECODED_MIME.get(img.format) != ref["mime_type"]:
        raise CompositionError("asset_mime_mismatch", f"{role} decodes as {img.format}, ref says {ref['mime_type']}",
                               [path], [ref["asset_version_id"]])
    if max(img.size) > MAX_SOURCE_EDGE:
        raise CompositionError("source_image_too_large", f"{role} exceeds the {MAX_SOURCE_EDGE}px longest-edge bound",
                               [path], [ref["asset_version_id"]])
    exif = img.getexif()
    if exif.get(0x0112, 1) != 1:
        raise CompositionError("orientation_not_normalized", f"{role} has EXIF orientation; import a normalized "
                               "derivative", [path], [ref["asset_version_id"]])
    icc = img.info.get("icc_profile")
    if icc:
        from PIL import ImageCms
        desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))) or ""
        if "srgb" not in desc.lower():
            raise CompositionError("color_profile_unsupported", f"{role} is not sRGB ({desc.strip()!r}); import an "
                                   "sRGB derivative", [path], [ref["asset_version_id"]])
    return img.convert("RGBA")


def _fit(img: Image.Image, box_w: int, box_h: int, fit: str) -> tuple[Image.Image, int, int]:
    """Returns the fitted surface and its offset inside the box."""
    sw, sh = img.size
    if fit == "native":
        return img, 0, 0
    scale = min(box_w / sw, box_h / sh) if fit == "contain" else max(box_w / sw, box_h / sh)
    tw, th = max(1, _r(sw * scale)), max(1, _r(sh * scale))
    resized = img.resize((tw, th), Image.Resampling.LANCZOS)
    if fit == "contain":
        return resized, (box_w - tw) // 2, (box_h - th) // 2
    left, top = (tw - box_w) // 2, (th - box_h) // 2
    return resized.crop((left, top, left + box_w, top + box_h)), 0, 0


def _box(t: dict) -> tuple[int, int]:
    return max(1, _r(t["width"])), max(1, _r(t["height"]))


def _render_image(ctx: _Context, layer: dict) -> Image.Image:
    c, t = layer["content"], layer["transform"]
    data = load_exact(ctx.assets, c["asset"], workspace_id=ctx.workspace_id, role="image",
                      field_path=ctx.path(layer, "/content/asset"))
    img = _decode_raster(data, c["asset"], "image", ctx.path(layer, "/content/asset"))
    if c["crop"]:
        cr = c["crop"]
        w, h = img.size
        img = img.crop((_r(cr["x"] * w), _r(cr["y"] * h), _r((cr["x"] + cr["width"]) * w),
                        _r((cr["y"] + cr["height"]) * h)))
    bw, bh = _box(t)
    fitted, ox, oy = _fit(img, bw, bh, c["fit"])
    surf = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    _composite(surf, fitted, ox, oy)
    ctx.sources.append({"layer_id": layer["id"], "role": "image", "asset": c["asset"]})
    return surf


def _render_logo(ctx: _Context, layer: dict, frame: _Frame) -> Image.Image:
    c, t = layer["content"], layer["transform"]
    path = ctx.path(layer, "/content")
    logo = next((l for l in ctx.package["logos"] if l["id"] == c["brand_logo_id"]), None)
    if logo is None:
        raise CompositionError("logo_asset_missing", f"brand package has no logo {c['brand_logo_id']}",
                               [path + "/brand_logo_id"], [ctx.package["version_id"]])
    if not (c["asset"]["sha256"] == logo["asset"]["sha256"] and c["asset"]["mime_type"] == logo["asset"]["mime_type"]):
        raise CompositionError("logo_not_from_brand_package", "logo layer bytes are not the package's exact logo file",
                               [path + "/asset"], [logo["id"]])
    if not any(same_ref(r["ref"], logo["asset"]) for r in ctx.package["assets"]):
        raise CompositionError("logo_asset_missing", "package logo is not present in package assets",
                               [path + "/asset"], [logo["id"]])
    if t["rotation_deg"] or t["opacity"] != 1 or frame.rotations:
        raise CompositionError("logo_transform_unsupported", "logos are placed unrotated at full opacity",
                               [ctx.path(layer, "/transform")], [logo["id"]])
    data = load_exact(ctx.assets, c["asset"], workspace_id=ctx.workspace_id, role="logo",
                      field_path=path + "/asset", missing_code="logo_asset_missing")
    img = _decode_raster(data, c["asset"], "logo", path + "/asset")
    bw, bh = _box(t)
    fitted, ox, oy = _fit(img, bw, bh, "contain")
    if fitted.width < logo["min_width_px"]:
        raise CompositionError("logo_below_min_width",
                               f"logo renders {fitted.width}px wide; package minimum is {logo['min_width_px']}px",
                               [ctx.path(layer, "/transform/width")], [logo["id"]])
    ax, ay = frame.ox + t["x"] + ox, frame.oy + t["y"] + oy
    clear = logo["clear_space_ratio"] * min(fitted.width, fitted.height)
    ctx.logo_clear.append((layer["id"], (ax - clear, ay - clear, ax + fitted.width + clear,
                                         ay + fitted.height + clear)))
    surf = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    _composite(surf, fitted, ox, oy)
    ctx.sources.append({"layer_id": layer["id"], "role": "logo", "brand_logo_id": logo["id"], "asset": c["asset"],
                        "drawn_px": [fitted.width, fitted.height], "clear_space_px": round(clear, 3)})
    return surf


def _font(ctx: _Context, layer: dict) -> tuple[bytes, fontinfo.FontInfo, dict]:
    ref = layer["content"]["font"]
    path = ctx.path(layer, "/content/font")
    typography = next((ty for ty in ctx.package["typography"]
                       if any(fa["sha256"] == ref["sha256"] for fa in ty["font_assets"])), None)
    if typography is None or _package_asset(ctx.package, ref["sha256"], ref["mime_type"]) is None:
        raise CompositionError("font_asset_missing", "text font is not a licensed font file of the pinned brand "
                               "package; choose a package font or record an accepted substitution revision",
                               [path], [ref["asset_version_id"]])
    if ref["asset_version_id"] not in ctx._font_cache:
        data = load_exact(ctx.assets, ref, workspace_id=ctx.workspace_id, role="font", field_path=path,
                          missing_code="font_asset_missing")
        ctx._font_cache[ref["asset_version_id"]] = (data, fontinfo.inspect(data))
    data, info = ctx._font_cache[ref["asset_version_id"]]
    return data, info, typography


def _measure(font, text: str, spacing: float) -> float:
    if not text:
        return 0.0
    if spacing == 0:
        return font.getlength(text)
    return sum(font.getlength(ch) for ch in text) + spacing * (len(text) - 1)


def _wrap(font, text: str, width: float, spacing: float) -> list[str] | None:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split(" ")
        line = words[0]
        for word in words[1:]:
            candidate = line + " " + word
            if _measure(font, candidate, spacing) <= width:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    if any(_measure(font, ln, spacing) > width for ln in lines):
        return None
    return lines


def _render_text(ctx: _Context, layer: dict, frame: _Frame) -> Image.Image:
    c, t, style = layer["content"], layer["transform"], layer["content"]["style"]
    path = ctx.path(layer, "/content")
    if not c["text"]:
        raise CompositionError("text_empty", "text layer has no copy", [path + "/text"])
    data, info, typography = _font(ctx, layer)
    if info.weight_class != style["weight"] or style["weight"] not in typography["weights"]:
        raise CompositionError("font_weight_missing",
                               f"weight {style['weight']} requested; font file is weight {info.weight_class} "
                               f"(package {typography['family']} lists {typography['weights']}); no synthesized bold",
                               [path + "/style/weight"], [c["font"]["asset_version_id"]])
    missing = info.missing(c["text"])
    if missing:
        raise CompositionError("glyph_missing", f"font lacks glyphs {', '.join(missing[:20])}; no substitute font is "
                               "used", [path + "/text"], [c["font"]["asset_version_id"]])
    font = ImageFont.truetype(io.BytesIO(data), size=style["font_size_px"], layout_engine=ImageFont.Layout.BASIC)
    bw, bh = _box(t)
    spacing = style["letter_spacing_px"]
    lines = _wrap(font, c["text"], bw, spacing)
    line_px = style["font_size_px"] * style["line_height"]
    if lines is None or len(lines) * line_px > bh:
        raise CompositionError("text_overflow", "copy does not fit its saved box at the saved style; change text, "
                               "box or style (no truncation or auto-shrink)", [path + "/text"], [layer["id"]])
    ascent, descent = font.getmetrics()
    block = len(lines) * line_px
    top = {"top": 0.0, "center": (bh - block) / 2, "bottom": bh - block}[style["vertical_align"]]
    surf = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(surf)
    fill = _rgba(style["fill"])
    for i, line in enumerate(lines):
        if not line:
            continue
        w = _measure(font, line, spacing)
        x = {"left": 0.0, "center": (bw - w) / 2, "right": bw - w}[style["align"]]
        y = _r(top + i * line_px + (line_px - (ascent + descent)) / 2)
        if spacing == 0:
            l, tp, r, b = font.getbbox(line, anchor="la")
            if _r(x) + l < 0 or y + tp < 0 or _r(x) + r > bw or y + b > bh:
                raise CompositionError("text_overflow", "glyph ink extends outside the text box", [path + "/text"],
                                       [layer["id"]])
            draw.text((_r(x), y), line, font=font, fill=fill, anchor="la")
        else:
            cursor = x
            for ch in line:
                draw.text((_r(cursor), y), ch, font=font, fill=fill, anchor="la")
                cursor += font.getlength(ch) + spacing
    if c["copy_id"] is not None:
        ctx.copy.append({"layer_id": layer["id"], "copy_id": c["copy_id"], "text": c["text"],
                         "text_sha256": text_sha256(c["text"])})
    else:
        ctx.copy.append({"layer_id": layer["id"], "copy_id": None, "text": c["text"],
                         "text_sha256": text_sha256(c["text"])})
    ctx.fonts.setdefault(c["font"]["sha256"], {"sha256": c["font"]["sha256"], "family": info.family,
                                               "weight": info.weight_class, "asset": c["font"],
                                               "typography_id": typography["id"]})
    ctx.text_layout.append({"layer_id": layer["id"], "font_sha256": c["font"]["sha256"], "lines": lines,
                            "line_height_px": line_px, "glyph_coverage": "complete", "layout_engine": "basic"})
    return surf


def _render_shape(layer: dict) -> Image.Image:
    c, t = layer["content"], layer["transform"]
    bw, bh = _box(t)
    surf = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(surf)
    stroke = _rgba(c["stroke"]) if c["stroke"] else None
    sw = _r(c["stroke_width_px"]) if stroke else 0
    box = (0, 0, bw - 1, bh - 1)
    if c["shape"] == "rectangle":
        draw.rounded_rectangle(box, radius=_r(c["corner_radius_px"]), fill=_rgba(c["fill"]), outline=stroke, width=sw)
    elif c["shape"] == "ellipse":
        draw.ellipse(box, fill=_rgba(c["fill"]), outline=stroke, width=sw)
    else:  # line: horizontal centre line of the box, stroke colour (fill when no stroke)
        draw.line((0, bh // 2, bw - 1, bh // 2), fill=stroke or _rgba(c["fill"]), width=max(1, sw or bh))
    return surf


def _draw_layers(ctx: _Context, canvas: Image.Image, layer_ids: list[str], frame: _Frame) -> None:
    layers = sorted((ctx.board["layers"][ctx.index[i]] for i in layer_ids), key=lambda l: (l["order"], l["id"]))
    for layer in layers:
        t = layer["transform"]
        if layer["type"] == "group":
            child_frame = frame.child(layer)
            surface = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            # Children draw at un-rotated absolute positions; the group surface is then rotated.
            # child_frame carries the rotation chain so placement checks are rotation-aware.
            _draw_layers(ctx, surface, layer["content"]["child_ids"], child_frame)
            if t["rotation_deg"]:
                cx, cy = frame.ox + t["x"] + t["width"] / 2, frame.oy + t["y"] + t["height"] / 2
                surface = surface.rotate(-t["rotation_deg"], resample=Image.Resampling.BICUBIC, center=(cx, cy))
            _composite(canvas, _with_opacity(surface, t["opacity"]), 0, 0)
            continue
        if layer["type"] == "image":
            surf = _render_image(ctx, layer)
        elif layer["type"] == "logo":
            surf = _render_logo(ctx, layer, frame)
        elif layer["type"] == "text":
            surf = _render_text(ctx, layer, frame)
        else:
            surf = _render_shape(layer)
        if layer["type"] in ("text", "logo"):
            ctx.keepout.append((layer["id"], frame.aabb(t)))
        if t["rotation_deg"]:
            surf = surf.rotate(-t["rotation_deg"], resample=Image.Resampling.BICUBIC, expand=True)
        cx, cy = frame.ox + t["x"] + t["width"] / 2, frame.oy + t["y"] + t["height"] / 2
        _composite(canvas, _with_opacity(surf, t["opacity"]), _r(cx - surf.width / 2), _r(cy - surf.height / 2))


def _check_placement(ctx: _Context, width: int, height: int) -> None:
    sx0, sy0, sx1, sy1 = ctx.safe_rect
    eps = 1e-9
    for lid, (x0, y0, x1, y1) in ctx.keepout:
        layer = ctx.board["layers"][ctx.index[lid]]
        if x0 < -eps or y0 < -eps or x1 > width + eps or y1 > height + eps:
            raise CompositionError("layer_out_of_bounds", f"{layer['type']} layer {lid} extends outside the artboard",
                                   [ctx.path(layer, "/transform")], [lid])
        if x0 < sx0 - eps or y0 < sy0 - eps or x1 > sx1 + eps or y1 > sy1 + eps:
            raise CompositionError("outside_safe_zone", f"{layer['type']} layer {lid} is outside the canvas safe zone",
                                   [ctx.path(layer, "/transform")], [lid])
    for lid, (x0, y0, x1, y1) in ctx.logo_clear:
        layer = ctx.board["layers"][ctx.index[lid]]
        if x0 < -eps or y0 < -eps or x1 > width + eps or y1 > height + eps:
            raise CompositionError("logo_clear_space_violation", f"logo {lid} clear space extends outside the artboard",
                                   [ctx.path(layer, "/transform")], [lid])
        for other, (ox0, oy0, ox1, oy1) in ctx.keepout:
            if other != lid and ox0 < x1 and ox1 > x0 and oy0 < y1 and oy1 > y0:
                raise CompositionError("logo_clear_space_violation",
                                       f"layer {other} intrudes on logo {lid} clear space",
                                       [ctx.path(layer, "/transform")], [lid, other])


def _encode(canvas: Image.Image, mime: str, background: tuple) -> bytes:
    buf = io.BytesIO()
    opaque = canvas.getchannel("A").getextrema()[0] == 255
    if mime == "image/png":
        (canvas.convert("RGB") if opaque else canvas).save(buf, "PNG", compress_level=6, optimize=False)
    elif mime == "image/jpeg":
        if background[3] != 255:
            raise CompositionError("jpeg_requires_opaque_background",
                                   "JPEG export needs an opaque artboard background to flatten against",
                                   ["/background"])
        flat = Image.new("RGBA", canvas.size, background)
        flat.alpha_composite(canvas)
        flat.convert("RGB").save(buf, "JPEG", quality=92, subsampling=0, optimize=False, progressive=False)
    else:
        (canvas.convert("RGB") if opaque else canvas).save(buf, "WEBP", lossless=True, quality=100, method=4,
                                                            exact=True)
    return buf.getvalue()


def render_artboard(composition: dict, artboard_id: str, *, brand_package: dict, assets: AssetSource,
                    safe_zone: SafeZone, mime_type: str = "image/png") -> RenderResult:
    validate_composition(composition)
    if composition["kind"] != "image":
        raise CompositionError("unsupported_composition_kind", "only image compositions render here", ["/kind"])
    if mime_type not in _ENCODERS:
        raise CompositionError("unsupported_export_mime", f"{mime_type} is not an image export type")
    if not isinstance(safe_zone, SafeZone):
        raise CompositionError("safe_zone_missing", "an explicit SafeZone (with source) is required per canvas")
    _check_package(brand_package)
    board_index = next((i for i, b in enumerate(composition["artboards"]) if b["id"] == artboard_id), None)
    if board_index is None:
        raise CompositionError("resource_unavailable", f"artboard {artboard_id} is not in this composition",
                               ["/artboards"])
    board = composition["artboards"][board_index]
    width, height = board["width"], board["height"]
    used = {l["content"][k]["asset_id"] for l in board["layers"] for k in ("asset", "font")
            if isinstance(l["content"], dict) and k in l["content"]}
    pkg_ids = {r["ref"]["asset_id"] for r in brand_package["assets"]
               if any(l["content"].get(k, {}).get("sha256") == r["ref"]["sha256"]
                      for l in board["layers"] for k in ("asset", "font") if isinstance(l["content"], dict))}
    blocking = _blocking_decisions(brand_package, used | pkg_ids)
    if blocking:
        raise CompositionError("brand_decision_unresolved", "brand package has unresolved blocking decisions for "
                               "assets used here", ["/brand_package"], blocking)
    ctx = _Context(composition, board, board_index, brand_package, assets, safe_zone.rect(width, height))
    background = _rgba(board["background"])
    canvas = Image.new("RGBA", (width, height), background)
    children = {c for l in board["layers"] if l["type"] == "group" for c in l["content"]["child_ids"]}
    top_level = [l["id"] for l in board["layers"] if l["id"] not in children]
    _draw_layers(ctx, canvas, top_level, _Frame())
    _check_placement(ctx, width, height)
    data = _encode(canvas, mime_type, background)
    digest = hashlib.sha256(data).hexdigest()
    version = renderer_version()
    receipt = {
        "renderer_version": version,
        "composition": {"id": composition["id"], "version_id": composition["version_id"],
                        "revision": composition["revision"], "sha256": composition["sha256"],
                        "semantic_sha256": semantic_sha256(composition)},
        "artboard": {"id": board["id"], "width": width, "height": height, "background": board["background"],
                     "color_space": "srgb"},
        "brand_package": {"id": brand_package["id"], "version_id": brand_package["version_id"],
                          "revision": brand_package["revision"], "sha256": brand_package["sha256"]},
        "safe_zone": safe_zone.as_record(width, height),
        "sources": ctx.sources,
        "fonts": sorted(ctx.fonts.values(), key=lambda f: f["sha256"]),
        "exact_copy": ctx.copy,
        "text_layout": ctx.text_layout,
        "output": {"sha256": digest, "mime_type": mime_type, "size_bytes": len(data), "width": width,
                   "height": height},
    }
    return RenderResult(data=data, sha256=digest, mime_type=mime_type, width=width, height=height,
                        renderer_version=version, receipt=receipt)
