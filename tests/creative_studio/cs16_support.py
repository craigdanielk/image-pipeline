"""CS-16 test support: static catalogue entries (shape of cs_plan_catalogue()) for the pure compiler goldens."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from service.providers.canonical import digest

FIX = Path(__file__).parent / "b2_fixtures"
GOLD = Path(__file__).parent / "b16_goldens"


def fixture(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def catalogue() -> list[dict]:
    def entry(entry_id, tier, unit, quality, surface="marketing_studio", model="0f6c2a7e-1d3b-4c55-9a10-5b7e8c9d0a11"):
        e = {"adapter_version": "engine_higgsfield@fixture", "request_schema_sha256": digest({"schema": entry_id})}
        return {"entry_id": entry_id, "provider": "higgsfield", "model_id": model,
                "endpoint_key": "marketing-studio-dtc-ads", "surface": surface, "tier": tier,
                "credential_modes": ["workspace", "platform"], "entry": e,
                "price_snapshot": {"quote_parameters": {"quality": quality}, "quoted_credits": "1"},
                "unit_cost_micros": unit, "max_units_per_request": 4, "sha256": digest({"e": entry_id, "u": unit})}
    return [entry("hf-ms-low", "draft", 50000, "low"), entry("hf-ms-high", "final", 325000, "high"),
            entry("hf-ms-pricey-draft", "draft", 90000, "medium")]


def three_canvas(request: dict) -> dict:
    r = copy.deepcopy(request)
    base = r["formats"][0]
    r["budget"]["max_cost_micros"] *= 3        # one budgeted unit per canvas
    r["formats"] = [
        dict(base, id="square_1080-slot-1"),
        dict(base, id="portrait_1080x1350-slot-2", preset="portrait_1080x1350", height_px=1350),
        dict(base, id="story_1080x1920-slot-3", preset="story_1080x1920", height_px=1920)]
    return r
