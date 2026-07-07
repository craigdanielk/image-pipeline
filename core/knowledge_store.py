#!/usr/bin/env python3
"""Knowledge store — ProductSpec / CreativeSpec v2 CRUD on disk."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import spec_validator_v2

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class KnowledgeError(ValueError):
    pass


def _slug(value: str, field: str = "id") -> str:
    s = (value or "").strip().lower().replace("_", "-")
    s = re.sub(r"[^a-z0-9-]+", "-", s).strip("-")
    if not s or not _SLUG_RE.match(s):
        raise KnowledgeError(f"{field} must be a lowercase slug [a-z0-9-], got {value!r}.")
    return s


def product_template(
    *,
    id: str,
    name: str,
    geometry_description: str,
    material_color: str = "neutral",
    material_type: str = "generic",
) -> dict[str, Any]:
    slug = _slug(id)
    return {
        "schema_version": "product-spec-v2",
        "id": slug,
        "name": name.strip(),
        "immutable": True,
        "product": {
            "geometry": {
                "description": geometry_description.strip(),
                "symmetry": "bilateral",
                "envelope": "product",
            },
            "materials": [
                {
                    "id": "primary",
                    "type": material_type,
                    "color": material_color,
                    "finish": "matte",
                    "texture": "smooth",
                }
            ],
            "dimensions": {"width_mm": 100, "height_mm": 100},
        },
        "brand": {"logos": []},
        "constraints": {
            "preserve_geometry": True,
            "preserve_material": True,
            "preserve_proportions": True,
        },
        "editing": {
            "protected_regions": ["product"],
            "editable_regions": ["background", "scene"],
        },
        "references": [],
        "source": {"knowledge_path": f"knowledge/products/{slug}.json"},
    }


def campaign_template(
    *,
    id: str,
    product_spec_id: str,
    label: str,
    mood: str = "studio",
    style: str = "product_hero",
    scene_description: str,
    lighting: str = "soft_key_fill, neutral backdrop",
    camera: str = "eye_level, product legible",
    background: str = "neutral_studio",
    composition: str = "product centered, clean negative space",
) -> dict[str, Any]:
    cid = _slug(id)
    pid = _slug(product_spec_id, "product_spec_id")
    return {
        "schema_version": "creative-spec-v2",
        "id": cid,
        "product_spec_id": pid,
        "campaign": {
            "id": cid,
            "label": label.strip(),
            "audience": "general",
            "mood": mood,
            "style": style,
        },
        "scene": {
            "description": scene_description.strip(),
            "lighting": lighting,
            "camera": camera,
            "composition": composition,
            "background": background,
        },
        "scene_locks": [],
        "source": {"knowledge_path": f"knowledge/campaigns/{cid}.json"},
    }


class KnowledgeStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.products_dir = self.root / "knowledge" / "products"
        self.campaigns_dir = self.root / "knowledge" / "campaigns"
        self.products_dir.mkdir(parents=True, exist_ok=True)
        self.campaigns_dir.mkdir(parents=True, exist_ok=True)

    def list_products(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in sorted(self.products_dir.glob("*.json")):
            data = self._read_json(path)
            out.append(self._product_summary(data, path))
        return out

    def get_product(self, slug: str) -> dict[str, Any]:
        path = self._product_path(slug)
        if not path.is_file():
            raise KnowledgeError(f"product not found: {slug}")
        return self._read_json(path)

    def save_product(self, spec: dict[str, Any]) -> dict[str, Any]:
        spec_validator_v2.validate_product_spec_v2(spec)
        slug = spec["id"]
        path = self._product_path(slug)
        path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        return self._product_summary(spec, path)

    def create_product(self, payload: dict[str, Any]) -> dict[str, Any]:
        spec = product_template(
            id=payload.get("id") or payload.get("slug") or payload.get("name", ""),
            name=payload.get("name", ""),
            geometry_description=payload.get("geometry_description", ""),
            material_color=payload.get("material_color", "neutral"),
            material_type=payload.get("material_type", "generic"),
        )
        path = self._product_path(spec["id"])
        if path.exists():
            raise KnowledgeError(f"product already exists: {spec['id']}")
        (path.parent / "refs").mkdir(parents=True, exist_ok=True)
        return self.save_product(spec)

    def list_campaigns(self, product_spec_id: str | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in sorted(self.campaigns_dir.glob("*.json")):
            data = self._read_json(path)
            if product_spec_id and data.get("product_spec_id") != product_spec_id:
                continue
            out.append(self._campaign_summary(data, path))
        return out

    def get_campaign(self, campaign_id: str) -> dict[str, Any]:
        path = self._find_campaign_path(campaign_id)
        if not path:
            raise KnowledgeError(f"campaign not found: {campaign_id}")
        return self._read_json(path)

    def save_campaign(self, spec: dict[str, Any]) -> dict[str, Any]:
        product = self.get_product(spec["product_spec_id"])
        spec_validator_v2.validate_creative_spec_v2(spec)
        spec_validator_v2.validate_spec_bundle_v2(product, spec, [])
        path = self.campaigns_dir / f"{_slug(spec['campaign']['id'])}.json"
        path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        return self._campaign_summary(spec, path)

    def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._product_path(payload.get("product_spec_id", "")).is_file():
            raise KnowledgeError(f"product not found: {payload.get('product_spec_id')}")
        spec = campaign_template(
            id=payload.get("id") or payload.get("label", ""),
            product_spec_id=payload["product_spec_id"],
            label=payload.get("label", ""),
            mood=payload.get("mood", "studio"),
            style=payload.get("style", "product_hero"),
            scene_description=payload.get("scene_description", ""),
            lighting=payload.get("lighting", "soft_key_fill"),
            camera=payload.get("camera", "eye_level"),
            background=payload.get("background", "neutral_studio"),
            composition=payload.get("composition", "product centered"),
        )
        path = self.campaigns_dir / f"{spec['campaign']['id']}.json"
        if path.exists():
            raise KnowledgeError(f"campaign already exists: {spec['id']}")
        return self.save_campaign(spec)

    def add_product_reference(
        self, slug: str, *, ref_id: str, role: str, asset_path: str
    ) -> dict[str, Any]:
        spec = self.get_product(slug)
        refs = list(spec.get("references") or [])
        if any(r.get("id") == ref_id for r in refs):
            raise KnowledgeError(f"reference id already exists: {ref_id}")
        refs.append({"id": ref_id, "role": role, "asset": asset_path})
        spec["references"] = refs
        return self.save_product(spec)

    def product_refs_dir(self, slug: str) -> Path:
        d = self.products_dir / _slug(slug) / "refs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _product_path(self, slug: str) -> Path:
        return self.products_dir / f"{_slug(slug)}.json"

    def _find_campaign_path(self, campaign_id: str) -> Path | None:
        direct = self.campaigns_dir / f"{_slug(campaign_id)}.json"
        if direct.is_file():
            return direct
        for path in self.campaigns_dir.glob("*.json"):
            data = self._read_json(path)
            if data.get("id") == campaign_id or path.stem == campaign_id:
                return path
        return None

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _product_summary(data: dict[str, Any], path: Path) -> dict[str, Any]:
        refs = data.get("references") or []
        logos = (data.get("brand") or {}).get("logos") or []
        return {
            "slug": data.get("id") or path.stem,
            "name": data.get("name") or path.stem,
            "geometry_description": (data.get("product") or {}).get("geometry", {}).get("description"),
            "reference_count": len(refs) + len(logos),
            "path": path.as_posix(),
        }

    @staticmethod
    def _campaign_summary(data: dict[str, Any], path: Path) -> dict[str, Any]:
        camp = data.get("campaign") or {}
        return {
            "id": data.get("id") or path.stem,
            "label": camp.get("label") or path.stem,
            "product_spec_id": data.get("product_spec_id"),
            "mood": camp.get("mood"),
            "scene": data.get("scene"),
            "path": path.as_posix(),
        }
