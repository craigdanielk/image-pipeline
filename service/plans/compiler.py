"""Deterministic in-process compile (no provider call, no network, no clock, no randomness).

request + accepted treatment + approved brand package + catalogue entries -> cs-execution-plan-v1 and a readable
media pack summary. Same inputs give the same bytes: ids are uuid5 over the pinned hashes, created_at/created_by are
taken from the request, and the revision is the only caller-supplied number (a function of prior plans).
Exact copy and logos are summarised as composition layers (B4 composes them); they never enter a model prompt.
"""
from __future__ import annotations

import re
import uuid

from ..brand.mos_brandkit import package_version_ref
from ..errors import ServiceError
from ..providers.canonical import artifact_digest, digest
from ..providers.contracts import ContractViolation, validate
from ..requests import compile_mapping
from ..requests.documents import version_ref

COMPILER_VERSION = "cs-16-hosted-compile-v1"
LOWERING_VERSION = "single-provider-render-per-canvas-v1"
NODE_KIND = "provider_generate"
NODE_IMPL = "cs-16-provider-generate-1"
_NS = uuid.UUID("5c5a5b0e-16c5-4d3e-9c4a-0c5e16c5a001")
_SLUG_BAD = re.compile(r"[^a-z0-9_-]+")


def _u5(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "|".join(parts)))


def node_id_for(fmt_id: str) -> str:
    return ("render-" + _SLUG_BAD.sub("-", fmt_id.lower()).strip("-"))[:64].rstrip("-_")


def select_entry(catalogue: list[dict], request: dict, tier: str) -> dict:
    sel = request["provider_selection"]
    mode = request["budget"]["funding_source"]
    cands = [e for e in catalogue if e["tier"] == tier and mode in e["credential_modes"]]
    if sel.get("provider"):
        cands = [e for e in cands if e["provider"] == sel["provider"]]
    if sel.get("model_id"):
        cands = [e for e in cands if e["model_id"] == sel["model_id"]]
    cap = sel.get("max_unit_cost_micros")
    if cap is not None:
        cands = [e for e in cands if e["unit_cost_micros"] <= cap]
    if not cands:
        raise ServiceError(409, "catalogue_unavailable", "no current priced catalogue entry fits this request")
    return sorted(cands, key=lambda e: (e["unit_cost_micros"], e["entry_id"]))[0]


_ASPECTS = {(1, 1): "1:1", (4, 5): "4:5", (9, 16): "9:16", (16, 9): "16:9"}


def _aspect(fmt: dict) -> str | None:
    w, h = fmt.get("width_px"), fmt.get("height_px")
    if not (isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0):
        return None
    a, b = w, h
    while b:
        a, b = b, a % b
    g = a
    return _ASPECTS.get((w // g, h // g))


def _params(entry: dict, units: int, fmt: dict | None = None) -> dict:
    params = {"batch_size": units}
    quote_params = (entry.get("price_snapshot") or {}).get("quote_parameters") or {}
    if entry["surface"] == "marketing_studio":
        params["format_id"] = entry["model_id"]
        params["quality"] = quote_params.get("quality")
    else:
        if quote_params.get("quality"):
            params["quality"] = quote_params["quality"]
        # The canvas shape and the priced resolution must reach the model; without them it renders its 1:1 default.
        if quote_params.get("resolution"):
            params["resolution"] = quote_params["resolution"]
        aspect = _aspect(fmt or {})
        if aspect:
            params["aspect_ratio"] = aspect
    return params


def _reference_bindings(entry: dict, references: list[dict]) -> tuple[list[dict], list[dict]]:
    """Request references (signed-off draft, product photos) become node inputs bound to the model's image field."""
    roles = (entry.get("entry") or {}).get("reference_roles") or []
    role = next((r for r in roles if r.get("role") == "image"), None)
    if not references:
        return [], []
    if role is None:
        if any(r.get("required") for r in references):
            raise ServiceError(409, "catalogue_unavailable", "the selected model takes no reference images")
        return [], []
    usable = references[: int(role.get("max_count") or 0)]
    if any(r.get("required") for r in references[len(usable):]):
        raise ServiceError(409, "catalogue_unavailable", "more required reference images than the model accepts")
    inputs = [{"port": "references", "source": {"kind": "asset", "asset": r["asset"]}} for r in usable]
    bindings = [{"field": role["field"], "role": "image", "input_port": "references", "ordinal": i} for i in range(len(usable))]
    return inputs, bindings


def content_key(request: dict, treatment: dict, package: dict, entries: list[dict], tier: str) -> str:
    return digest({"request": request["sha256"], "treatment": treatment["sha256"], "brand_package": package["sha256"],
                   "catalogue": sorted((e["entry_id"], e["sha256"]) for e in entries), "tier": tier,
                   "compiler": COMPILER_VERSION, "lowering": LOWERING_VERSION})


def brand_elements(package: dict) -> dict:
    return {
        "logos": [{"id": l["id"], "role": l["role"], "asset_version_id": l["asset"]["asset_version_id"],
                   "sha256": l["asset"]["sha256"]} for l in package["logos"]],
        "palette": [{"role": p["role"], "hex": p["hex"], "token": p["token"]} for p in package["palette"]],
        "fonts": [{"id": t["id"], "family": t["family"], "role": t["role"],
                   "font_assets": [{"asset_version_id": a["asset_version_id"], "sha256": a["sha256"]}
                                   for a in t["font_assets"]]} for t in package["typography"]],
        "voice": package["voice"]}


def compile_plan(*, request: dict, treatment: dict, package: dict, catalogue: list[dict], tier: str, revision: int,
                 product_spec: dict | None = None) -> tuple[dict, dict, str, list[dict]]:
    """Returns (plan, summary, content_key, entries_used)."""
    if treatment.get("status") != "accepted":
        raise ServiceError(409, "treatment_not_frozen", "only an accepted treatment revision compiles")
    nodes, outputs, canvases, used = [], [], [], {}
    for fmt in request["formats"]:
        mapping = compile_mapping.compile_inputs({**request, "formats": [fmt]}, treatment, package, product_spec)
        entry = select_entry(catalogue, request, tier)
        used[entry["entry_id"]] = entry
        prompt = mapping["provider_prompt"]
        units = fmt["count"]
        if units > entry["max_units_per_request"]:
            raise ServiceError(409, "catalogue_unavailable",
                               f"canvas {fmt['id']} asks {units} images; entry allows {entry['max_units_per_request']}")
        nid = node_id_for(fmt["id"])
        cost = entry["unit_cost_micros"] * units
        e = entry["entry"]
        ref_inputs, ref_bindings = _reference_bindings(entry, request.get("references") or [])
        nodes.append({
            "id": nid, "kind": NODE_KIND, "implementation_version": NODE_IMPL, "dependencies": [], "inputs": ref_inputs,
            "provider_call": {
                "provider": entry["provider"], "model_id": entry["model_id"], "catalogue_entry_id": entry["entry_id"],
                "endpoint_key": entry["endpoint_key"], "adapter_version": e["adapter_version"],
                "request_schema_sha256": e["request_schema_sha256"], "positive_prompt": prompt["positive"],
                "negative_prompt": prompt.get("negative"), "parameters": _params(entry, units, fmt), "media_bindings": ref_bindings,
                "seed": None,
                "retries": {"automatic_resubmits": 0, "transport_before_dispatch_max": 2, "same_intent_replay_max": 0}},
            "transform": None,
            "output_contract": [{"port": "image", "media_kind": fmt["kind"], "mime_type": fmt["mime_type"],
                                 "width_px": fmt["width_px"], "height_px": fmt["height_px"], "duration_frames": None}],
            "estimated_cost_micros": cost, "maximum_cost_micros": cost})
        outputs.append({"id": f"out-{nid}"[:64], "node_id": nid, "port": "image", "format": fmt})
        canvases.append({"format_id": fmt["id"], "preset": fmt["preset"], "width_px": fmt["width_px"],
                         "height_px": fmt["height_px"], "count": units, "node_id": nid, "lane": mapping["lane"],
                         "prompt": {"positive": prompt["positive"], "negative": prompt.get("negative")},
                         "model": {"provider": entry["provider"], "model_id": entry["model_id"],
                                   "catalogue_entry_id": entry["entry_id"]}})
    entries = [used[k] for k in sorted(used)]
    total = sum(n["maximum_cost_micros"] for n in nodes)
    if total > request["budget"]["max_cost_micros"]:
        raise ServiceError(409, "plan_over_budget",
                           f"plan maximum {total} exceeds the request budget {request['budget']['max_cost_micros']}")
    ckey = content_key(request, treatment, package, entries, tier)
    cat_sha = digest(sorted((e["entry_id"], e["sha256"]) for e in entries))
    graph_sha = digest({"lowering": LOWERING_VERSION, "nodes": [{"id": n["id"], "kind": n["kind"],
                                                                "dependencies": n["dependencies"]} for n in nodes]})
    ws = request["workspace_id"]
    program = {
        "schema_version": "cs-program-v1", "compiler_version": COMPILER_VERSION, "lowering_version": LOWERING_VERSION,
        "node_registry_sha256": digest({"registry": [NODE_KIND, NODE_IMPL]}), "catalogue_sha256": cat_sha,
        "template_sha256": None,
        "source_semantics": {"request_sha256": compile_mapping.request_semantic_sha256(request),
                             "treatment_sha256": compile_mapping.treatment_semantic_sha256(treatment),
                             "recipe_graph_sha256": graph_sha, "composition_sha256": None},
        "asset_inputs": [], "nodes": nodes, "outputs": outputs, "budget_ceiling_micros": total, "currency": "USD"}
    compile_hash = digest(program)
    refs = {"request": version_ref(request, "creative-request"), "treatment": version_ref(treatment, "treatment"),
            "recipe": {"authority": "creative-studio", "workspace_id": ws, "kind": "recipe",
                       "id": _u5("builtin-recipe", LOWERING_VERSION), "version_id": _u5("builtin-recipe-v", graph_sha),
                       "revision": 1, "sha256": graph_sha},
            "composition": None, "brand_package": package_version_ref(package),
            "catalogue": {"authority": "creative-studio", "workspace_id": ws, "kind": "provider_catalogue",
                          "id": _u5("catalogue", cat_sha), "version_id": _u5("catalogue-v", cat_sha), "revision": 1,
                          "sha256": cat_sha}}
    plan = {
        "schema_version": "cs-execution-plan-v1", "id": _u5("plan", ckey), "version_id": _u5("plan-v", ckey, str(revision)),
        "revision": revision, "workspace_id": ws, "created_at": request["created_at"], "created_by": request["created_by"],
        "sha256": "", "compile_hash_algorithm": "sha256-rfc8785-cs-program-v1", "compile_hash": compile_hash,
        "program": program, "sources": refs,
        "admission": {"budget": request["budget"], "estimated_cost_micros": total, "maximum_cost_micros": total,
                      "expires_at": request["budget"]["expires_at"]},
        "legacy_artifacts": []}
    plan["sha256"] = artifact_digest(plan)
    try:
        validate("execution-plan-v1.schema.json", plan)
    except ContractViolation as exc:
        raise ServiceError(422, "plan_invalid", "; ".join(exc.errors[:3])) from None
    summary = {
        "plan_id": plan["id"], "plan_version_id": plan["version_id"], "plan_sha256": plan["sha256"],
        "compile_hash": compile_hash, "revision": revision, "tier": tier,
        "pinned": {"treatment": refs["treatment"], "request": refs["request"], "brand_package": refs["brand_package"],
                   "catalogue": refs["catalogue"]},
        "canvases": canvases, "brand_elements": brand_elements(package),
        "text_layers": [{"id": c["id"], "role": c["role"], "text": c["text"], "language": c["language"],
                         "composed_by": "composition", "model_drawn": False} for c in request["exact_copy"]],
        "references": {"count": len(request["references"]),
                       "bound_to_provider_call": any(n["provider_call"]["media_bindings"] for n in plan["program"]["nodes"])},
        "estimated_credits": None, "quote_status": "unquoted"}
    return plan, summary, ckey, entries
