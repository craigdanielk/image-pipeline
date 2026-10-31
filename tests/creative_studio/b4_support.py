"""Shared loaders for CS-B4 tests (fixtures are provisional; see b4_fixtures/README.md)."""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

from service.composition.assets import MemoryAssetSource
from service.composition.canonical import artifact_sha256
from service.composition.safe_zones import SafeZone

FIXTURES = Path(__file__).resolve().parent / "b4_fixtures"
GOLDENS = Path(__file__).resolve().parent / "b4_goldens"
FILES = {"logo": "example-logo-primary.png", "font": "aileron-regular.ttf", "scene": "example-generated-scene.png"}


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def asset_bytes(key: str) -> bytes:
    return (FIXTURES / "assets" / FILES[key]).read_bytes()


class World:
    def __init__(self):
        self.package = load("brand_package.json")
        self.records = load("studio_assets.json")
        self.composition = load("composition.json")
        self.lineage = load("lineage.json")
        self.target = load("mos_target.json")
        self.copy_sources = load("copy_sources.json")
        self.safe_zones = load("safe_zones.json")
        self.ids = load("ids.json")
        self.source = MemoryAssetSource()
        for key in FILES:
            self.source.put(self.records[key]["ref"], asset_bytes(key))

    def zone(self, board_id: str) -> SafeZone:
        board = self.board(board_id)
        spec = self.safe_zones[board_id]
        if spec is None:
            return SafeZone.artboard_bounds()
        return SafeZone.from_percent(board["width"], board["height"], **spec)

    def board(self, board_id: str, comp: dict | None = None) -> dict:
        return next(b for b in (comp or self.composition)["artboards"] if b["id"] == board_id)

    def layer(self, comp: dict, board_id: str, layer_id: str) -> dict:
        return next(l for l in self.board(board_id, comp)["layers"] if l["id"] == layer_id)

    def edited(self, fn) -> dict:
        """A new composition revision produced by `fn(doc)`, re-hashed like a saved version."""
        doc = copy.deepcopy(self.composition)
        fn(doc)
        doc["revision"] += 1
        doc["version_id"] = str(uuid.uuid5(uuid.UUID(doc["id"]), f"revision-{doc['revision']}"))
        doc["sha256"] = artifact_sha256(doc)
        return doc
