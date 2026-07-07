#!/usr/bin/env python3
"""Compile validation gate — fail hard before PromptCompiler v2."""
from __future__ import annotations

from typing import Any

import spec_validator
import spec_validator_v2


class CompileGateError(ValueError):
    """Raised when specs fail validation and compile must abort."""


def validate_for_compile(
    product_spec: dict[str, Any],
    creative_spec: dict[str, Any],
    execution_request: dict[str, Any] | None = None,
    *,
    edit_specs: list[dict[str, Any]] | None = None,
) -> str:
    """Validate spec bundle. Returns schema lane: 'v1' or 'v2'. Raises on failure."""
    pv = product_spec.get("schema_version", "")
    cv = creative_spec.get("schema_version", "")

    if pv == spec_validator_v2.PRODUCT_SCHEMA_V2 or cv == spec_validator_v2.CREATIVE_SCHEMA_V2:
        try:
            spec_validator_v2.validate_spec_bundle_v2(product_spec, creative_spec, edit_specs)
        except spec_validator_v2.SpecV2Error as exc:
            raise CompileGateError(str(exc)) from exc
        lane = "v2"
    else:
        spec_validator.validate_product_spec(product_spec)
        spec_validator.validate_creative_spec(creative_spec)
        for edit in edit_specs or []:
            spec_validator.validate_edit_spec(edit)
        lane = "v1"

    if execution_request is not None:
        spec_validator.validate_execution_request(execution_request)

    if lane == "v2":
        for edit in edit_specs or []:
            if edit.get("schema_version") != spec_validator_v2.EDIT_SCHEMA_V2:
                raise CompileGateError(
                    f"EditSpec {edit.get('id')!r} must be edit-spec-v2 when product/creative are v2."
                )

    return lane


def assert_no_prose_v2(spec: dict[str, Any], label: str) -> None:
    """Extra gate: v2 specs must not carry transitional prose fields."""
    banned = ("geometry_text", "prompt_template", "only_change", "instruction")
    for key in banned:
        if key in spec:
            raise CompileGateError(f"{label} contains forbidden prose field '{key}'.")
