"""Creative catalogue v1: ad formats x shot archetypes x layouts (client-agnostic, no database, no network)."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import jsonschema
import pytest
import yaml

from service.catalogue import CatalogueError, get_format, get_shot, load_catalogue

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schemas" / "creative-catalogue-v1.schema.json").read_text())
EXPORT_CANVASES = {"1:1", "4:5", "9:16", "16:9"}
# Placeholders a template may use without declaring a slot: craft defaults, then client/compiler keys.
CRAFT_KEYS = {"lens", "aperture", "light", "angle", "finish"}
CLIENT_KEYS = {"scene_palette", "text_space"}
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


@pytest.fixture(scope="module")
def cat() -> dict:
    return load_catalogue()


def test_catalogue_validates_against_schema(cat):
    jsonschema.validate(cat, SCHEMA)


def test_ids_unique_and_cross_refs_resolve(cat):
    for kind in ("formats", "shots", "layouts"):
        ids = [item["id"] for item in cat[kind]]
        assert len(ids) == len(set(ids)), f"duplicate {kind} ids"
    shot_ids = {s["id"] for s in cat["shots"]}
    layout_ids = {lay["id"] for lay in cat["layouts"]}
    for fmt in cat["formats"]:
        assert set(fmt["shots_allowed"]) <= shot_ids, fmt["id"]
        assert fmt["layout"] in layout_ids, fmt["id"]


def test_v1_ids_are_fixed(cat):
    assert {f["id"] for f in cat["formats"]} == {
        "feed_single_image", "story_vertical", "carousel_card", "landscape_banner", "library_plate"}
    assert {s["id"] for s in cat["shots"]} == {
        "lifestyle_moment", "outcome_confidence", "delivery_moment", "hands_detail", "product_hero", "flat_lay",
        "environment_scene", "founder_team"}


def test_canvases_are_export_presets(cat):
    assert {c for f in cat["formats"] for c in f["canvases"]} <= EXPORT_CANVASES


def test_format_canvases_match_plan(cat):
    expected = {"feed_single_image": ["1:1", "4:5"], "story_vertical": ["9:16"], "carousel_card": ["4:5"],
                "landscape_banner": ["16:9"], "library_plate": ["1:1", "4:5", "9:16"]}
    assert {f["id"]: f["canvases"] for f in cat["formats"]} == expected


def test_channel_formats_exist_in_mos_channel_specs(cat):
    mos_repo = os.environ.get("MOS_REPO")
    if not mos_repo:
        pytest.skip("MOS_REPO not set")
    specs = yaml.safe_load((Path(mos_repo) / "registry" / "channel-specs.yaml").read_text())
    known = {f"{c['key']}.{f['key']}" for c in specs["channels"] for f in c.get("formats") or []}
    for fmt in cat["formats"]:
        missing = set(fmt["channel_formats"]) - known
        assert not missing, f"{fmt['id']}: {sorted(missing)}"


def test_founder_team_never_generated(cat):
    assert get_shot(cat, "founder_team")["people"] == "owner_photo_only"


def test_people_and_product_rules(cat):
    assert get_shot(cat, "environment_scene")["people"] == "none"
    assert get_shot(cat, "environment_scene")["text_zone"] == "none"
    for shot_id in ("product_hero", "flat_lay"):
        assert get_shot(cat, shot_id)["requires_product_image"] is True
    assert get_shot(cat, "lifestyle_moment")["people"] == "buyer_models"


def test_library_plate_has_no_text_layers(cat):
    layers = get_format(cat, "library_plate")["text_layers"]
    assert layers["headline_max"] == layers["body_max"] == layers["cta_max"] == 0
    assert layers["disclaimer"] is False


def test_lifestyle_moment_template_is_exact(cat):
    shot = get_shot(cat, "lifestyle_moment")
    assert shot["prompt_template"] == (
        "Editorial lifestyle photograph. {subject} {action} in {setting}, {expression}. {light}. {scene_palette}. "
        "Shot on {lens}, {aperture}, {angle}, {finish}. {text_space}. No text, no logos.")
    bounds = {s["key"]: (s["min_chars"], s["max_chars"]) for s in shot["slots"]}
    assert bounds == {"subject": (12, 90), "action": (6, 80), "setting": (6, 90), "expression": (4, 60)}


def test_every_template_slot_declared(cat):
    for shot in cat["shots"]:
        used = set(PLACEHOLDER.findall(shot["prompt_template"]))
        declared = {s["key"] for s in shot["slots"]}
        assert used - CRAFT_KEYS - CLIENT_KEYS == declared, shot["id"]
        assert not declared & (CRAFT_KEYS | CLIENT_KEYS), shot["id"]


def test_templates_are_short_and_negatives_present(cat):
    for shot in cat["shots"]:
        assert len(shot["prompt_template"]) <= 400, shot["id"]
        assert shot["negative"], shot["id"]


def test_unknown_ids_raise_typed_errors(cat):
    with pytest.raises(CatalogueError) as exc:
        get_format(cat, "nope")
    assert exc.value.code == "unknown_format"
    with pytest.raises(CatalogueError) as exc:
        get_shot(cat, "nope")
    assert exc.value.code == "unknown_shot"


def test_invalid_catalogue_is_refused(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("formats: []\nshots: []\n")
    with pytest.raises(CatalogueError) as exc:
        load_catalogue(str(bad))
    assert exc.value.code == "invalid_catalogue"
    with pytest.raises(CatalogueError) as exc:
        load_catalogue(str(tmp_path / "missing.yaml"))
    assert exc.value.code == "catalogue_unavailable"


def test_load_is_cached_by_path():
    assert load_catalogue() is load_catalogue()
