#!/usr/bin/env python3
"""CapabilityResolver — ProviderPrompt + ExecutionRequest → ProviderExecutionPlan."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import spec_validator

# Provider-native model capabilities (registry v1 — inline until externalized).
_CAPABILITIES: dict[str, dict[str, dict[str, Any]]] = {
    "higgsfield": {
        "recraft_v4_1": {
            "accepts_reference": False,
            "batch_size": True,
            "model_type": True,
            "colors": True,
            "resolution": ["1k", "2k"],
            "negative_prompt": False,
        },
        "nano_banana_2": {
            "accepts_reference": True,
            "batch_size": False,
            "model_type": False,
            "colors": False,
            "resolution": ["1k", "2k", "4k", "auto"],
            "negative_prompt": False,
        },
        "gpt_image_2": {
            "accepts_reference": True,
            "batch_size": False,
            "model_type": False,
            "colors": False,
            "resolution": ["1k", "2k"],
            "negative_prompt": False,
        },
    },
}


class CapabilityResolverError(ValueError):
    """Raised when no valid execution plan can be built."""


def _canonical_json(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _plan_hash(plan: dict[str, Any]) -> str:
    payload = copy.deepcopy(plan)
    payload.get("metadata", {}).pop("content_hash", None)
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _model_caps(provider: str, model: str) -> dict[str, Any]:
    family = _CAPABILITIES.get(provider)
    if not family:
        raise CapabilityResolverError(f"Unknown provider family {provider!r}.")
    caps = family.get(model)
    if not caps:
        raise CapabilityResolverError(
            f"No capability registry entry for {provider}:{model}."
        )
    return caps


def resolve(
    provider_prompt: dict[str, Any],
    execution_request: dict[str, Any],
    *,
    job_dir: str = ".",
) -> dict[str, Any]:
    """Build a deterministic ProviderExecutionPlan from compile outputs."""
    spec_validator.validate_provider_prompt(provider_prompt)
    spec_validator.validate_execution_request(execution_request)

    engine = execution_request["engine"]
    output = execution_request["output"]
    provider = engine["name"]
    model = engine["model"]
    caps = _model_caps(provider, model)

    refs = provider_prompt.get("input_assets", {}).get("references") or []
    has_ref = bool(execution_request.get("reference")) or bool(refs)
    if has_ref and not caps.get("accepts_reference"):
        raise CapabilityResolverError(
            f"Model {provider}:{model} does not accept reference images."
        )

    params: dict[str, Any] = dict(engine.get("params") or {})
    if output.get("aspect_ratio"):
        params["aspect_ratio"] = output["aspect_ratio"]
    if output.get("resolution"):
        params["resolution"] = output["resolution"]
    count = output.get("count", 1)
    if count and int(count) > 1 and caps.get("batch_size"):
        params["batch_size"] = int(count)
    if engine.get("model_type") and caps.get("model_type"):
        params["model_type"] = engine["model_type"]
    if output.get("colors") and caps.get("colors"):
        params["colors"] = output["colors"]

    negative = provider_prompt.get("negative")
    if negative and caps.get("negative_prompt"):
        params["negative"] = negative
    elif negative:
        params["_negative_appended"] = True

    method = "cost_estimate" if execution_request.get("dry_run") else "generate"

    plan: dict[str, Any] = {
        "schema_version": spec_validator.EXECUTION_PLAN_SCHEMA,
        "id": execution_request["id"] + "-plan",
        "provider_prompt_id": provider_prompt["metadata"]["content_hash"],
        "provider": provider,
        "model": model,
        "method": method,
        "params": params,
        "uploads": [],
        "media_inputs": list(params.get("input_images") or []),
        "retries": {"max_attempts": 3, "backoff": "exponential"},
        "masks": [],
        "metadata": {
            "capability_registry_version": "v1-inline",
            "job_dir": job_dir,
            "accepts_reference": caps.get("accepts_reference", False),
        },
        "prompt": {
            "positive": provider_prompt["positive"],
            "negative": negative,
        },
        "output": copy.deepcopy(output),
    }
    plan["metadata"]["content_hash"] = _plan_hash(plan)
    spec_validator.validate_provider_execution_plan(plan)
    return plan
