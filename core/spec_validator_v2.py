#!/usr/bin/env python3
"""Spec validation v2 — structured languages, prose rejection."""
from __future__ import annotations

import re
from typing import Any

PRODUCT_SCHEMA_V2 = "product-spec-v2"
CREATIVE_SCHEMA_V2 = "creative-spec-v2"
EDIT_SCHEMA_V2 = "edit-spec-v2"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_PROSE_BAN_RE = re.compile(r"ONLY\s+CHANGE", re.IGNORECASE)
_V1_FORBIDDEN_KEYS = frozenset(
    {
        "geometry_text",
        "prompt_template",
        "negative_template",
        "only_change",
        "instruction",
        "surgical_delta",
    }
)

_VALID_OPS = frozenset(
    {
        "replace_logo",
        "replace_child",
        "remove_element",
        "insert_mark",
        "region_edit",
        "subject_swap",
        "increase_spacing",
        "reposition_logo",
        "remove_background",
        "change_colour",
    }
)


class SpecV2Error(ValueError):
    """Raised when a v2 spec is malformed or contains forbidden prose."""


def _check_slug(value: str, field: str) -> None:
    if not value or not _SLUG_RE.match(value):
        raise SpecV2Error(f"{field} must be a lowercase slug [a-z0-9-], got {value!r}.")


def _scan_prose(obj: Any, path: str = "") -> None:
    """Reject v1 prose keys and ONLY CHANGE fragments anywhere in tree."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            full = f"{path}.{key}" if path else key
            if key in _V1_FORBIDDEN_KEYS:
                raise SpecV2Error(f"Forbidden v1 prose field '{full}'.")
            _scan_prose(val, full)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _scan_prose(item, f"{path}[{i}]")
    elif isinstance(obj, str):
        if _PROSE_BAN_RE.search(obj):
            raise SpecV2Error(f"Forbidden prose pattern at '{path}': ONLY CHANGE.")


def validate_product_spec_v2(spec: dict) -> bool:
    if not isinstance(spec, dict):
        raise SpecV2Error("ProductSpec v2 must be a JSON object.")
    if spec.get("schema_version") != PRODUCT_SCHEMA_V2:
        raise SpecV2Error(f"schema_version must be '{PRODUCT_SCHEMA_V2}'.")
    _scan_prose(spec)
    _check_slug(spec.get("id", ""), "id")
    if not spec.get("name"):
        raise SpecV2Error("name is required.")
    product = spec.get("product")
    if not isinstance(product, dict):
        raise SpecV2Error("product is required.")
    geometry = product.get("geometry")
    if not isinstance(geometry, dict) or not geometry.get("description"):
        raise SpecV2Error("product.geometry.description is required.")
    constraints = spec.get("constraints")
    if not isinstance(constraints, dict):
        raise SpecV2Error("constraints is required.")
    for key in ("preserve_geometry", "preserve_material", "preserve_proportions"):
        if key not in constraints:
            raise SpecV2Error(f"constraints.{key} is required.")
    editing = spec.get("editing")
    if not isinstance(editing, dict):
        raise SpecV2Error("editing is required.")
    if "protected_regions" not in editing or "editable_regions" not in editing:
        raise SpecV2Error("editing.protected_regions and editable_regions are required.")
    return True


def validate_creative_spec_v2(spec: dict) -> bool:
    if not isinstance(spec, dict):
        raise SpecV2Error("CreativeSpec v2 must be a JSON object.")
    if spec.get("schema_version") != CREATIVE_SCHEMA_V2:
        raise SpecV2Error(f"schema_version must be '{CREATIVE_SCHEMA_V2}'.")
    _scan_prose(spec)
    _check_slug(spec.get("id", ""), "id")
    if not spec.get("product_spec_id"):
        raise SpecV2Error("product_spec_id is required.")
    campaign = spec.get("campaign")
    if not isinstance(campaign, dict) or not campaign.get("id"):
        raise SpecV2Error("campaign.id is required.")
    scene = spec.get("scene")
    if not isinstance(scene, dict) or not scene.get("description"):
        raise SpecV2Error("scene.description is required.")
    return True


def validate_edit_spec_v2(spec: dict) -> bool:
    if not isinstance(spec, dict):
        raise SpecV2Error("EditSpec v2 must be a JSON object.")
    if spec.get("schema_version") != EDIT_SCHEMA_V2:
        raise SpecV2Error(f"schema_version must be '{EDIT_SCHEMA_V2}'.")
    _scan_prose(spec)
    _check_slug(spec.get("id", ""), "id")
    ops = spec.get("operations")
    if not isinstance(ops, list) or not ops:
        raise SpecV2Error("operations must be a non-empty array.")
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            raise SpecV2Error(f"operations[{i}] must be an object.")
        name = op.get("op")
        if name not in _VALID_OPS:
            raise SpecV2Error(f"operations[{i}].op invalid: {name!r}.")
        if name in ("replace_logo", "subject_swap", "insert_mark") and not op.get("asset"):
            raise SpecV2Error(f"operations[{i}] requires asset for op={name}.")
        if name in ("replace_logo", "remove_element", "reposition_logo", "region_edit") and not (
            op.get("target") or op.get("region")
        ):
            raise SpecV2Error(f"operations[{i}] requires target or region for op={name}.")
    constraints = spec.get("constraints")
    if not isinstance(constraints, dict):
        raise SpecV2Error("constraints is required.")
    if "preserve" not in constraints or "forbidden" not in constraints:
        raise SpecV2Error("constraints.preserve and forbidden are required.")
    parent = spec.get("parent")
    if parent is not None:
        if not isinstance(parent, dict) or not parent.get("asset_id"):
            raise SpecV2Error("parent.asset_id is required when parent is set.")
    return True


def validate_spec_bundle_v2(
    product_spec: dict,
    creative_spec: dict,
    edit_specs: list[dict] | None = None,
) -> bool:
    """Cross-spec consistency checks."""
    validate_product_spec_v2(product_spec)
    validate_creative_spec_v2(creative_spec)
    if creative_spec["product_spec_id"] != product_spec["id"]:
        raise SpecV2Error(
            f"creative_spec.product_spec_id {creative_spec['product_spec_id']!r} "
            f"!= product_spec.id {product_spec['id']!r}."
        )
    for edit in edit_specs or []:
        validate_edit_spec_v2(edit)
        cid = edit.get("creative_spec_id")
        if cid and cid != creative_spec["id"]:
            raise SpecV2Error(
                f"edit_spec {edit['id']!r} creative_spec_id mismatch."
            )
    return True
