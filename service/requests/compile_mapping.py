"""Frozen request + accepted treatment + approved brand package -> existing compiler inputs.

Two lanes (contract §6, compatibility.md), and the legacy compiler is not modified:

* ``studio-physical`` — a physical offering with a real ProductSpec v2: emits a
  creative-spec-v2 from the accepted treatment, validates the pair with the
  unchanged ``compile_validator.validate_for_compile`` and assembles the prompt
  with the unchanged ``prompt_compiler_v2.compile_v2``.
* ``studio-offering`` — services/service groups (and offerings without a
  ProductSpec): reuses the compiler's scene assembly (``_scene_block``) and adds
  sourced service context. No ProductSpec, geometry, CAD or preservation flags
  are created, and the result is still a schema-valid ProviderPrompt v1.

Exact copy and logos are composition layers, so the prompt only reserves space
for them. Compilation never calls a model and never rewrites the treatment.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from ..brand.errors import InputRefusal
from .canonical import jcs_sha256, request_semantic_sha256, treatment_semantic_sha256

_CORE = Path(__file__).resolve().parents[2] / "core"
SERVICE_KINDS = ("service", "service_group")
MAPPING_VERSION = "cs-b2-compile-mapping-v1"


def _core():
    if str(_CORE) not in sys.path:
        sys.path.insert(0, str(_CORE))
    import compile_validator  # noqa: E402  (legacy modules import each other by bare name)
    import prompt_compiler_v2  # noqa: E402
    import spec_validator  # noqa: E402
    return compile_validator, prompt_compiler_v2, spec_validator


def _check_lineage(request: dict, treatment: dict, package: dict) -> None:
    if treatment.get("status") != "accepted":
        raise InputRefusal("treatment_not_accepted", "only an accepted treatment revision compiles", ["/status"])
    src = treatment["source_request"]
    if (src["id"], src["version_id"], src["sha256"]) != (request["id"], request["version_id"], request["sha256"]):
        raise InputRefusal("source_revision_changed", "treatment was accepted for another request revision",
                           ["/source_request"])
    bp = request["brand_package"]
    if (bp["version_id"], bp["sha256"]) != (package["version_id"], package["sha256"]):
        raise InputRefusal("source_revision_changed", "request pins another brand package version", ["/brand_package"])


def _creative_spec(request: dict, treatment: dict, product_spec_id: str | None) -> dict:
    d = treatment["direction"]
    spec = {
        "schema_version": "creative-spec-v2",
        "id": f"cs-{request['id'][:8]}-r{request['revision']}",
        "campaign": {"id": request["id"], "label": request["hook"], "style": d["style"]},
        "scene": {"description": f"{d['summary']}. {d['scene']}", "lighting": d["lighting"], "camera": d["camera"],
                  "composition": d["composition"]},
    }
    if product_spec_id is not None:
        spec["product_spec_id"] = product_spec_id
    return spec


def _service_block(offering: dict) -> list[str]:
    parts = [f"Offering: {offering['name']} — {offering['summary']}"]
    for vp in offering["value_propositions"]:
        parts.append(f"Value: {vp}")
    for fact in sorted(offering["service_facts"], key=lambda f: f["id"]):
        parts.append(f"{fact['label']}: {fact['value']}")
    return parts


def _brand_block(package: dict) -> list[str]:
    parts = []
    if package["imagery"]["principles"]:
        parts.append("Imagery: " + "; ".join(package["imagery"]["principles"]))
    palette = ", ".join(f"{p['role']} {p['hex']}" for p in package["palette"])
    parts.append(f"Brand palette: {palette}")
    return parts


def _overlay_block(request: dict) -> str:
    roles = sorted({c["role"] for c in request["exact_copy"]})
    return ("Leave clean, uncluttered space for the brand logo and exact " + ", ".join(roles)
            + " overlays; do not render any text, letters or logos in the image")


def compile_inputs(request: dict, treatment: dict, package: dict, product_spec: dict | None = None) -> dict:
    _check_lineage(request, treatment, package)
    compile_validator, prompt_compiler_v2, spec_validator = _core()
    offering = request["offering"]
    kind = offering["kind"]
    fmt = request["formats"][0]
    # legacy ids are lowercase [a-z0-9-] slugs
    execution_id = re.sub(r"[^a-z0-9-]+", "-", f"cs-{request['id'][:8]}-{fmt['id']}")[:63].rstrip("-")
    negatives = list(dict.fromkeys(treatment["direction"]["negative"] + request["constraints"]["forbidden"]))
    sources = {"request_semantic_sha256": request_semantic_sha256(request),
               "treatment_semantic_sha256": treatment_semantic_sha256(treatment),
               "brand_package_sha256": package["sha256"]}

    if kind in SERVICE_KINDS and offering["physical_product_spec"] is not None:
        raise InputRefusal("offering_geometry_forbidden", "services carry no ProductSpec",
                           ["/offering/physical_product_spec"])

    if kind == "physical_product" and offering["physical_product_spec"] is not None:
        if product_spec is None:
            raise InputRefusal("physical_product_spec_required", "the pinned ProductSpec document was not supplied",
                               ["/offering/physical_product_spec"])
        if jcs_sha256(product_spec) != offering["physical_product_spec"]["sha256"]:
            raise InputRefusal("asset_hash_mismatch", "ProductSpec differs from the pinned version",
                               ["/offering/physical_product_spec"])
        creative = _creative_spec(request, treatment, product_spec["id"])
        sel = request["provider_selection"]
        execution = {"schema_version": "execution-request-v1", "id": execution_id,
                     "output": {"aspect_ratio": f"{fmt['width_px']}:{fmt['height_px']}", "count": fmt["count"]},
                     "engine": {"name": sel["provider"] or "catalogue", "model": sel["model_id"] or "auto"},
                     "dry_run": True}
        try:
            compile_validator.validate_for_compile(product_spec, creative, execution)
            prompt = prompt_compiler_v2.compile_v2(product_spec, creative, execution)
        except (compile_validator.CompileGateError, prompt_compiler_v2.PromptCompilerV2Error,
                spec_validator.SpecError) as exc:
            raise InputRefusal("invalid_contract", f"legacy compiler refused: {exc}") from None
        lane = "studio-physical"
        legacy = {"product_spec": product_spec, "creative_spec": creative, "execution_request": execution}
    else:
        # service / service_group / digital / hybrid / physical without a ProductSpec
        creative = _creative_spec(request, treatment, None)
        sections = prompt_compiler_v2._scene_block(creative) + _service_block(offering) + _brand_block(package)
        sections.append(_overlay_block(request))
        positive = " ".join(s.strip().rstrip(".") + "." for s in sections if s.strip())
        prompt = {
            "schema_version": spec_validator.PROVIDER_SCHEMA,
            "positive": positive,
            "negative": " ".join(f"No {n.rstrip('.')}." for n in negatives) or None,
            "metadata": {"execution_id": execution_id, "creative_spec_id": creative["id"], "edit_spec_ids": [],
                         "placeholders_resolved": {}, "composition": treatment["direction"]["composition"]},
            "input_assets": {"references": [r["asset"]["asset_version_id"] for r in request["references"]],
                             "input_images": []},
        }
        prompt["metadata"]["content_hash"] = prompt_compiler_v2._content_hash(prompt)
        try:
            spec_validator.validate_provider_prompt(prompt)
        except spec_validator.SpecError as exc:
            raise InputRefusal("invalid_contract", f"provider prompt invalid: {exc}") from None
        lane = "studio-offering"
        legacy = {"product_spec": None, "creative_spec_scene": creative}
    out = {"mapping_version": MAPPING_VERSION, "lane": lane, "offering_kind": kind, "format": fmt,
           "provider_prompt": prompt, "legacy_inputs": legacy, "sources": sources}
    out["mapping_sha256"] = jcs_sha256(out)
    return out
