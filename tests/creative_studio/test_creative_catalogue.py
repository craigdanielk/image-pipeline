"""Creative catalogue v1: ad formats x shot archetypes x layouts (client-agnostic, no database, no network)."""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

import jsonschema
import pytest
import yaml

from service.catalogue import CatalogueError, get_format, get_shot, load_catalogue, validate_catalogue

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schemas" / "creative-catalogue-v1.schema.json").read_text())
EXPORT_CANVASES = {"1:1", "4:5", "9:16", "16:9"}
# Placeholders a template may use without declaring a slot: craft defaults, then client/compiler keys.
CRAFT_KEYS = {"lens", "aperture", "light", "angle", "finish"}
CLIENT_KEYS = {"scene_palette", "text_space", "realism"}
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


@pytest.fixture(scope="module")
def cat() -> dict:
    return load_catalogue()


def test_catalogue_validates_against_schema(cat):
    jsonschema.validate(cat, SCHEMA)


def test_schema_version_and_semver(cat):
    assert cat["schema_version"] == 1
    assert cat["version"] == "1.2.0"
    assert cat["compatible_with"] == ["1.1.0"]   # 1.2.0 only added shots
    bad = copy.deepcopy(cat)
    bad["version"] = "1.0"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SCHEMA)


def _has_text_layers(fmt: dict) -> bool:
    layers = fmt["text_layers"]
    return layers["headline_max"] + layers["body_max"] + layers["cta_max"] > 0 or layers["disclaimer"]


def test_text_formats_never_allow_text_free_shots(cat):
    zones = {s["id"]: s["text_zone"] for s in cat["shots"]}
    for fmt in cat["formats"]:
        if _has_text_layers(fmt):
            assert all(zones[s] != "none" for s in fmt["shots_allowed"]), fmt["id"]
    assert "environment_scene" not in get_format(cat, "landscape_banner")["shots_allowed"]


def test_craft_light_is_setting_neutral(cat):
    for shot in cat["shots"]:
        light = shot["craft"]["light"].lower()
        for word in ("window", "afternoon", "morning", "evening", "sunset", "indoor", "outdoor"):
            assert word not in light, (shot["id"], word)


def test_environment_scene_is_architectural(cat):
    shot = get_shot(cat, "environment_scene")
    assert shot["prompt_template"].startswith("Architectural photograph.")
    assert "interior" not in shot["prompt_template"].lower()


def test_outcome_confidence_is_natural_portrait(cat):
    shot = get_shot(cat, "outcome_confidence")
    assert shot["prompt_template"].startswith("Natural portrait photograph.")
    assert "candid" not in json.dumps(shot).lower()


def _refusal(cat: dict, mutate) -> str:
    bad = copy.deepcopy(cat)
    mutate(bad)
    with pytest.raises(CatalogueError) as exc:
        validate_catalogue(bad)
    return exc.value.code


def test_loader_refuses_text_format_with_text_free_shot(cat):
    code = _refusal(cat, lambda c: get_format(c, "feed_single_image")["shots_allowed"].append("environment_scene"))
    assert code == "text_zone_conflict"


def test_loader_refuses_undeclared_placeholder(cat):
    def mutate(c):
        get_shot(c, "lifestyle_moment")["prompt_template"] += " {mood}."
    assert _refusal(cat, mutate) == "undeclared_placeholder"


def test_loader_refuses_duplicate_slot_key(cat):
    def mutate(c):
        shot = get_shot(c, "hands_detail")
        shot["slots"].append(copy.deepcopy(shot["slots"][0]))
    assert _refusal(cat, mutate) == "duplicate_slot"


def test_loader_refuses_unused_slot(cat):
    def mutate(c):
        get_shot(c, "flat_lay")["slots"].append({"key": "mood", "required": False, "min_chars": 1, "max_chars": 20})
    assert _refusal(cat, mutate) == "undeclared_placeholder"


def test_loader_refuses_slot_shadowing_craft_key(cat):
    def mutate(c):
        shot = get_shot(c, "flat_lay")
        shot["slots"].append({"key": "light", "required": True, "min_chars": 1, "max_chars": 20})
    assert _refusal(cat, mutate) == "reserved_slot_key"


def test_loader_refuses_inverted_slot_bounds(cat):
    def mutate(c):
        get_shot(c, "product_hero")["slots"][0].update(min_chars=50, max_chars=10)
    assert _refusal(cat, mutate) == "invalid_slot_bounds"


def test_loader_accepts_the_shipped_catalogue(cat):
    validate_catalogue(copy.deepcopy(cat))


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
        "environment_scene", "founder_team", "pattern_interrupt", "visual_metaphor"}


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
        "Candid lifestyle photograph. {subject} {action} in {setting}, {expression}. {realism}. {light}. {scene_palette}. "
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


def test_realism_banks_and_forbidden_words(cat):
    from service.catalogue.creative import forbidden_in
    words = cat["realism"]["forbidden_words"]
    assert {"cinematic", "studio", "perfect", "professional", "editorial"} <= set(words)
    for shot in cat["shots"]:
        positive = " ".join([shot["prompt_template"], *shot["craft"].values()])
        assert not forbidden_in(positive, words), shot["id"]
        assert ("{realism}" in shot["prompt_template"]) == bool(shot["realism"]), shot["id"]
    assert get_shot(cat, "lifestyle_moment")["realism"] == ["skin", "pose", "imperfection"]
    assert get_shot(cat, "product_hero")["realism"] == [], "product shots stay exact"
    # whole words only: "professionally" and "studios" are not the forbidden words
    assert forbidden_in("professionally composed", words) == []
    assert forbidden_in("a Studio light", words) == ["studio"]


def test_loader_refuses_forbidden_word_in_catalogue(cat):
    import copy
    from service.catalogue.creative import validate_catalogue
    bad = copy.deepcopy(cat)
    get_shot(bad, "lifestyle_moment")["craft"]["finish"] = "cinematic colour grade"
    with pytest.raises(CatalogueError) as e:
        validate_catalogue(bad)
    assert e.value.code == "forbidden_word"
    bad = copy.deepcopy(cat)
    get_shot(bad, "product_hero")["realism"] = ["imperfection"]
    with pytest.raises(CatalogueError) as e:
        validate_catalogue(bad)
    assert e.value.code == "realism_mismatch"
