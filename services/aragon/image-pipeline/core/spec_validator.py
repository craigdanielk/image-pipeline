#!/usr/bin/env python3
"""Spec validation — dep-free hand validators for three-spec compile model."""
from __future__ import annotations

import re

PRODUCT_SCHEMA = "product-spec-v1"
CREATIVE_SCHEMA = "creative-spec-v1"
EDIT_SCHEMA = "edit-spec-v1"
EXECUTION_SCHEMA = "execution-request-v1"
PROVIDER_SCHEMA = "provider-prompt-v1"
EXECUTION_PLAN_SCHEMA = "provider-execution-plan-v1"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class SpecError(ValueError):
    """Raised when a compile-input or output spec is malformed."""


def _check_slug(value: str, field: str) -> None:
    if not value or not _SLUG_RE.match(value):
        raise SpecError(f"{field} must be a lowercase slug [a-z0-9-], got {value!r}.")


def validate_product_spec(spec: dict) -> bool:
    if not isinstance(spec, dict):
        raise SpecError("ProductSpec must be a JSON object.")
    if spec.get("schema_version") != PRODUCT_SCHEMA:
        raise SpecError(f"schema_version must be '{PRODUCT_SCHEMA}'.")
    _check_slug(spec.get("id", ""), "id")
    if not spec.get("name"):
        raise SpecError("name is required.")
    if not spec.get("geometry_text"):
        raise SpecError("geometry_text is required.")
    return True


def validate_creative_spec(spec: dict) -> bool:
    if not isinstance(spec, dict):
        raise SpecError("CreativeSpec must be a JSON object.")
    if spec.get("schema_version") != CREATIVE_SCHEMA:
        raise SpecError(f"schema_version must be '{CREATIVE_SCHEMA}'.")
    _check_slug(spec.get("id", ""), "id")
    if not spec.get("product_spec_id"):
        raise SpecError("product_spec_id is required.")
    if not spec.get("prompt_template"):
        raise SpecError("prompt_template is required.")
    return True


def validate_execution_request(spec: dict) -> bool:
    if not isinstance(spec, dict):
        raise SpecError("ExecutionRequest must be a JSON object.")
    if spec.get("schema_version") != EXECUTION_SCHEMA:
        raise SpecError(f"schema_version must be '{EXECUTION_SCHEMA}'.")
    _check_slug(spec.get("id", ""), "id")
    engine = spec.get("engine")
    if not isinstance(engine, dict) or not engine.get("name") or not engine.get("model"):
        raise SpecError("engine.name and engine.model are required.")
    out = spec.get("output")
    if not isinstance(out, dict):
        raise SpecError("output must be an object.")
    count = out.get("count", 1)
    if not isinstance(count, int) or not (1 <= count <= 4):
        raise SpecError(f"output.count must be an integer 1..4, got {count!r}.")
    ref = spec.get("reference")
    if ref is not None and not isinstance(ref, (str, dict, list)):
        raise SpecError("reference must be null, string, object, or array.")
    if isinstance(ref, dict) and not ref.get("image"):
        raise SpecError("reference object requires an 'image' field.")
    return True


def validate_edit_spec(spec: dict) -> bool:
    if not isinstance(spec, dict):
        raise SpecError("EditSpec must be a JSON object.")
    if spec.get("schema_version") != EDIT_SCHEMA:
        raise SpecError(f"schema_version must be '{EDIT_SCHEMA}'.")
    _check_slug(spec.get("id", ""), "id")
    if not spec.get("operation"):
        raise SpecError("operation is required.")
    if not spec.get("parent_asset_id"):
        raise SpecError("parent_asset_id is required.")
    return True


def validate_provider_execution_plan(spec: dict) -> bool:
    if not isinstance(spec, dict):
        raise SpecError("ProviderExecutionPlan must be a JSON object.")
    if spec.get("schema_version") != EXECUTION_PLAN_SCHEMA:
        raise SpecError(f"schema_version must be '{EXECUTION_PLAN_SCHEMA}'.")
    _check_slug(spec.get("id", ""), "id")
    if not spec.get("provider"):
        raise SpecError("provider is required.")
    if not spec.get("model"):
        raise SpecError("model is required.")
    if not spec.get("method"):
        raise SpecError("method is required.")
    if not isinstance(spec.get("params"), dict):
        raise SpecError("params must be an object.")
    return True


def validate_provider_prompt(spec: dict) -> bool:
    if not isinstance(spec, dict):
        raise SpecError("ProviderPrompt must be a JSON object.")
    if spec.get("schema_version") != PROVIDER_SCHEMA:
        raise SpecError(f"schema_version must be '{PROVIDER_SCHEMA}'.")
    if not spec.get("positive"):
        raise SpecError("positive prompt is required.")
    meta = spec.get("metadata")
    if not isinstance(meta, dict) or "execution_id" not in meta:
        raise SpecError("metadata.execution_id is required.")
    assets = spec.get("input_assets")
    if not isinstance(assets, dict):
        raise SpecError("input_assets is required.")
    if "references" not in assets or "input_images" not in assets:
        raise SpecError("input_assets.references and input_assets.input_images are required.")
    return True
