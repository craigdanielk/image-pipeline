"""Load and look up the creative catalogue (registry/creative-catalogue.yaml).

The catalogue is static, versioned data, so it is validated once per path and cached; callers must treat the
returned dict as read-only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import yaml

_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = _ROOT / "registry" / "creative-catalogue.yaml"
_SCHEMA_PATH = _ROOT / "schemas" / "creative-catalogue-v1.schema.json"
_CACHE: dict[str, dict] = {}
# Placeholders a template may use without a slot: craft defaults, then keys the client pack/compiler supplies.
CRAFT_KEYS = frozenset({"lens", "aperture", "light", "angle", "finish"})
CLIENT_KEYS = frozenset({"scene_palette", "text_space"})
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class CatalogueError(Exception):
    """A typed catalogue refusal: unreadable/invalid catalogue or an unknown id."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _check_refs(cat: dict) -> None:
    """Rules the schema cannot express: unique ids, resolvable refs, text-zone compatibility."""
    for kind in ("formats", "shots", "layouts"):
        ids = [item["id"] for item in cat[kind]]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise CatalogueError("invalid_catalogue", f"duplicate {kind} ids: {dupes}")
    zones = {s["id"]: s["text_zone"] for s in cat["shots"]}
    layout_ids = {lay["id"] for lay in cat["layouts"]}
    for fmt in cat["formats"]:
        unknown = sorted(set(fmt["shots_allowed"]) - set(zones))
        if unknown:
            raise CatalogueError("invalid_catalogue", f"format {fmt['id']} allows unknown shots {unknown}")
        if fmt["layout"] not in layout_ids:
            raise CatalogueError("invalid_catalogue", f"format {fmt['id']} uses unknown layout {fmt['layout']}")
        layers = fmt["text_layers"]
        has_text = layers["headline_max"] + layers["body_max"] + layers["cta_max"] > 0 or layers["disclaimer"]
        # text_zone "none" means the shot carries no text at all, so it cannot serve a format that composites text.
        text_free = sorted(s for s in fmt["shots_allowed"] if zones[s] == "none")
        if has_text and text_free:
            raise CatalogueError("text_zone_conflict", f"format {fmt['id']} has text layers but allows {text_free}")


def _check_shot_slots(shot: dict) -> None:
    """Template placeholders and declared slots must match exactly, so the compiler never leaves a hole."""
    keys = [slot["key"] for slot in shot["slots"]]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise CatalogueError("duplicate_slot", f"shot {shot['id']} declares slots {dupes} twice")
    shadowed = sorted(set(keys) & (CRAFT_KEYS | CLIENT_KEYS))
    if shadowed:
        raise CatalogueError("reserved_slot_key", f"shot {shot['id']} slots {shadowed} shadow craft/client keys")
    for slot in shot["slots"]:
        if slot["min_chars"] > slot["max_chars"]:
            raise CatalogueError("invalid_slot_bounds", f"shot {shot['id']} slot {slot['key']} min_chars > max_chars")
    used = set(_PLACEHOLDER.findall(shot["prompt_template"])) - CRAFT_KEYS - CLIENT_KEYS
    if used != set(keys):
        raise CatalogueError(
            "undeclared_placeholder",
            f"shot {shot['id']}: undeclared {sorted(used - set(keys))}, unused slots {sorted(set(keys) - used)}")


def validate_catalogue(cat: dict, schema: dict | None = None) -> None:
    """Raise CatalogueError unless `cat` is a valid creative catalogue."""
    if schema is None:
        try:
            schema = json.loads(_SCHEMA_PATH.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise CatalogueError("catalogue_unavailable", f"cannot read schema: {exc}") from exc
    try:
        jsonschema.validate(cat, schema)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        raise CatalogueError("invalid_catalogue", f"{where}: {exc.message}") from exc
    _check_refs(cat)
    for shot in cat["shots"]:
        _check_shot_slots(shot)


def load_catalogue(path: str | None = None) -> dict:
    key = str(Path(path).resolve()) if path else str(DEFAULT_PATH)
    if key in _CACHE:
        return _CACHE[key]
    try:
        cat = yaml.safe_load(Path(key).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise CatalogueError("catalogue_unavailable", f"cannot read catalogue {key}: {exc}") from exc
    validate_catalogue(cat)
    _CACHE[key] = cat
    return cat


def _by_id(items: list[dict], item_id: str, code: str) -> dict:
    for item in items:
        if item["id"] == item_id:
            return item
    raise CatalogueError(code, f"no catalogue entry with id {item_id!r}")


def get_format(cat: dict, format_id: str) -> dict:
    return _by_id(cat["formats"], format_id, "unknown_format")


def get_shot(cat: dict, shot_id: str) -> dict:
    return _by_id(cat["shots"], shot_id, "unknown_shot")


# -- shot compilation ----------------------------------------------------------------------------------------
POSITIVE_MAX = 1200
NEGATIVE_MAX = 400
LOGO_CORNER = "Leave one clean corner for a small logo"
_TEXT_SPACE = {
    "top_left": "Upper-left third is plain and empty for a headline",
    "top": "Upper third is plain and empty for a headline",
    "bottom": "Lower third is plain and empty for a headline",
    "left": "Left third is plain and empty for a headline",
}


def _check_slots(shot: dict, slots: dict) -> None:
    declared = {s["key"]: s for s in shot["slots"]}
    for key in sorted(set(slots) - set(declared)):
        raise CatalogueError("slot_invalid", f"slot {key!r} is not a slot of shot {shot['id']}")
    for key, spec in declared.items():
        value = slots.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            if spec["required"]:
                raise CatalogueError("slot_invalid", f"required slot {key!r} is missing")
            continue
        if not isinstance(value, str):
            raise CatalogueError("slot_invalid", f"slot {key!r} must be text")
        n = len(value.strip())
        if not spec["min_chars"] <= n <= spec["max_chars"]:
            raise CatalogueError("slot_invalid", f"slot {key!r} is {n} chars; allowed {spec['min_chars']}-{spec['max_chars']}")
        if "enum" in spec and value.strip() not in spec["enum"]:
            raise CatalogueError("slot_invalid", f"slot {key!r} must be one of {spec['enum']}")


def _text_space(shot: dict, fmt: dict) -> str:
    # "none" means no text at all, and a logo-only layout carries no headline, so neither asks for headline space.
    if shot["text_zone"] == "none" or fmt["layout"] == "logo_only":
        return LOGO_CORNER
    return _TEXT_SPACE[shot["text_zone"]]


def _palette_line(client: dict) -> str:
    cues = [f"{c['object']} in {c['hex']}" for c in client.get("scene_palette") or []]
    return "Colour cues: " + ", ".join(cues) if cues else ""


def _compact_restriction(text: str) -> str:
    text = text.strip().rstrip(".")
    return re.sub(r"^(no|avoid)\s+", "", text, flags=re.IGNORECASE)


def _negative(shot: dict, client: dict) -> str:
    """Client restrictions first (they are legal/brand limits and must never be dropped), then the shot's own.

    Restrictions that alone overflow the cap are refused; shot negatives fill what is left, whole phrases only."""
    restrictions = [_compact_restriction(r) for r in client.get("restrictions") or []]
    phrases: list[str] = []
    seen: set[str] = set()
    for phrase in restrictions + [n.strip() for n in shot["negative"]]:
        if phrase and phrase.lower() not in seen:
            seen.add(phrase.lower())
            phrases.append(phrase)
    fixed = phrases[:len({r.lower() for r in restrictions if r})]   # restrictions lead the deduped list
    if len(", ".join(fixed)) > NEGATIVE_MAX:
        raise CatalogueError("prompt_too_long", f"client restrictions alone exceed {NEGATIVE_MAX} chars")
    kept: list[str] = []
    for phrase in phrases:
        if len(", ".join(kept + [phrase])) <= NEGATIVE_MAX:
            kept.append(phrase)
    return ", ".join(kept)


def shot_prompt(shot: dict, slots: dict, client: dict, canvas: str, *, fmt: dict) -> dict:
    """Compile a short photographic prompt: {"positive", "negative"}. Never truncates the positive prompt."""
    if shot["people"] == "owner_photo_only":
        raise CatalogueError("shot_not_generatable", f"shot {shot['id']} is an owner photograph and is never generated")
    if canvas not in fmt["canvases"]:
        raise CatalogueError("canvas_invalid", f"canvas {canvas!r} is not allowed for format {fmt['id']}: {fmt['canvases']}")
    _check_slots(shot, slots)
    values = {**shot["craft"], **{k: v.strip() for k, v in slots.items()},
              "scene_palette": _palette_line(client), "text_space": _text_space(shot, fmt)}
    body = _PLACEHOLDER.sub(lambda m: values.get(m.group(1), ""), shot["prompt_template"])
    body = re.sub(r"\s+", " ", re.sub(r"(\.\s*)+\.", ".", body)).strip()
    style = (client.get("style_line") or "").strip()
    positive = f"{style.rstrip('.')}. {body}" if style else body
    if len(positive) > POSITIVE_MAX:
        raise CatalogueError("prompt_too_long", f"positive prompt is {len(positive)} chars; limit {POSITIVE_MAX}")
    return {"positive": positive, "negative": _negative(shot, client)}
