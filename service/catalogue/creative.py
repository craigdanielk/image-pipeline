"""Load and look up the creative catalogue (registry/creative-catalogue.yaml).

The catalogue is static, versioned data, so it is validated once per path and cached; callers must treat the
returned dict as read-only.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml

_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = _ROOT / "registry" / "creative-catalogue.yaml"
_SCHEMA_PATH = _ROOT / "schemas" / "creative-catalogue-v1.schema.json"
_CACHE: dict[str, dict] = {}


class CatalogueError(Exception):
    """A typed catalogue refusal: unreadable/invalid catalogue or an unknown id."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _check_refs(cat: dict) -> None:
    """Schema cannot express cross-references; enforce unique ids and resolvable shot/layout refs here."""
    for kind in ("formats", "shots", "layouts"):
        ids = [item["id"] for item in cat[kind]]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise CatalogueError("invalid_catalogue", f"duplicate {kind} ids: {dupes}")
    shot_ids = {s["id"] for s in cat["shots"]}
    layout_ids = {lay["id"] for lay in cat["layouts"]}
    for fmt in cat["formats"]:
        unknown = sorted(set(fmt["shots_allowed"]) - shot_ids)
        if unknown:
            raise CatalogueError("invalid_catalogue", f"format {fmt['id']} allows unknown shots {unknown}")
        if fmt["layout"] not in layout_ids:
            raise CatalogueError("invalid_catalogue", f"format {fmt['id']} uses unknown layout {fmt['layout']}")


def load_catalogue(path: str | None = None) -> dict:
    key = str(Path(path).resolve()) if path else str(DEFAULT_PATH)
    if key in _CACHE:
        return _CACHE[key]
    try:
        cat = yaml.safe_load(Path(key).read_text())
        schema = json.loads(_SCHEMA_PATH.read_text())
    except (OSError, yaml.YAMLError, json.JSONDecodeError) as exc:
        raise CatalogueError("catalogue_unavailable", f"cannot read catalogue {key}: {exc}") from exc
    try:
        jsonschema.validate(cat, schema)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        raise CatalogueError("invalid_catalogue", f"{where}: {exc.message}") from exc
    _check_refs(cat)
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
