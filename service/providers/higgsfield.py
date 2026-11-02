"""Hosted wrapper around the single Higgsfield adapter (core/adapters/engine_higgsfield.py).

All provider traffic goes through that adapter: quotes via its `generate cost` /
`dtc-ads --cost-only`, dispatch via `execute_plan`. This module only (a) maps a
frozen plan provider_call to the adapter's plan shape, (b) normalises the
adapter's results and per-call `charge` into receipt rows, and (c) classifies
failures so the runtime never guesses about money:

  refused    the adapter raised a typed refusal that it raises strictly BEFORE
             its paid create call -> provably not charged.
  failed     the provider reported a terminal non-success -> definite failure,
             charge unknown unless evidenced.
  uncertain  anything else after the dispatch marker -> may have been charged;
             the CLI transport carries no provider idempotency key, so there is
             no same-intent replay: the attempt goes to reconciliation.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from .adapter import engine_higgsfield as hf

from .canonical import digest

PROVIDER_ID = hf.PROVIDER_ID
ADAPTER_REVISION = "engine_higgsfield@" + hashlib.sha256(Path(hf.__file__).read_bytes()).hexdigest()[:16]

# Typed refusals the adapter raises before `_metered_create` (validation,
# catalogue, credentials). Verified against engine_higgsfield.py: _dtc_base,
# _media_refs, _cli_env, load/parse_catalogue all run before the create call.
PRE_DISPATCH_REFUSALS = frozenset({
    "provider_credentials_missing", "provider_catalogue_missing", "provider_catalogue_invalid",
    "quality_unknown", "ad_format_unknown", "prompt_missing", "brand_kit_id_invalid", "aspect_ratio_unsupported",
    "resolution_unsupported", "batch_size_invalid", "references_exceed_limit", "reference_invalid",
    "provider_runtime_unavailable",
})
# Refusals raised after the provider answered with a terminal job state.
TERMINAL_FAILURES = frozenset({"generation_not_completed"})


@dataclass
class ProviderOutcome:
    outcome: str                      # completed | failed
    jobs: list = field(default_factory=list)
    charges: list = field(default_factory=list)
    error: dict | None = None


def error_doc(code: str, message: str, *, retryable: bool = False, related: list | None = None) -> dict:
    """common.schema.json#/$defs/error shape."""
    return {"code": code[:100], "message": (message or code)[:2000], "retryable": retryable,
            "field_paths": [], "related_ids": [str(r)[:100] for r in (related or [])][:30]}


def classify(exc: BaseException) -> tuple[str, dict]:
    if isinstance(exc, hf.ProviderRefusal):
        if exc.code in PRE_DISPATCH_REFUSALS:
            return "refused", error_doc(exc.code, exc.detail or exc.code)
        if exc.code in TERMINAL_FAILURES:
            return "failed", error_doc(exc.code, exc.detail or exc.code)
        return "uncertain", error_doc(exc.code, exc.detail or exc.code)
    return "uncertain", error_doc("dispatch_outcome_unknown",
                                  f"{type(exc).__name__}: provider call ended without a readable result")


def credits_from_cost(response) -> Decimal:
    """Credits from the adapter's cost JSON (the adapter's own parser)."""
    return hf._credits_from_cost(response)


def micros_for_credits(credits: Decimal, credit_price_micros: int) -> int:
    return int((Decimal(credits) * Decimal(credit_price_micros)).to_integral_value(rounding=ROUND_CEILING))


def _catalogue(adapter_config: dict):
    return hf.parse_catalogue(adapter_config["catalogue"])


def _split_parameters(parameters: dict) -> tuple[dict, dict]:
    params = dict(parameters or {})
    output = {}
    count = params.pop("batch_size", None)
    if count is not None:
        output["count"] = int(count)
    for key in ("aspect_ratio", "resolution"):
        if key in params:
            output[key] = params.pop(key)
    return params, output


def adapter_plan(surface: str, provider_call: dict, adapter_config: dict, reference_paths: list[dict]) -> tuple[dict, object]:
    """Map a frozen provider_call to engine_higgsfield.execute_plan(plan, reference)."""
    params, output = _split_parameters(provider_call.get("parameters") or {})
    prompt = {"positive": provider_call["positive_prompt"], "negative": provider_call.get("negative_prompt")}
    if prompt["negative"] and not (adapter_config.get("controls") or {}).get("negative_prompt", False):
        params["_negative_appended"] = True
    if surface == "marketing_studio":
        params.setdefault("format_id", provider_call["model_id"])
        plan = {"method": hf.MARKETING_STUDIO_METHOD, "provider": PROVIDER_ID, "model": provider_call["model_id"],
                "params": params, "prompt": prompt, "output": output,
                "media_inputs": [{"path": r["path"], "role": r.get("role") or "image"} for r in reference_paths]}
        return plan, None
    plan = {"provider": PROVIDER_ID, "model": provider_call["model_id"], "params": params, "prompt": prompt,
            "output": output}
    refs = [r["path"] for r in reference_paths]
    return plan, (refs or None)


def quote_credits(surface: str, model_id: str, parameters: dict, probe_prompt: str, adapter_config: dict,
                  credential) -> Decimal:
    """Ask the provider's own metering (no job is created) for one request's credits."""
    call = {"model_id": model_id, "positive_prompt": probe_prompt, "negative_prompt": None, "parameters": parameters}
    plan, _ = adapter_plan(surface, call, adapter_config, [])
    if surface == "marketing_studio":
        plan["method"] = hf.MARKETING_STUDIO_COST_METHOD
        res = hf.execute_plan(plan, None, dry_run=True, credential=credential, catalogue=_catalogue(adapter_config))
    else:
        res = hf.execute_plan(plan, None, dry_run=True, credential=credential)
    return credits_from_cost(res)


def execute(surface: str, provider_call: dict, adapter_config: dict, reference_paths: list[dict], credential,
            job_dir: str) -> list:
    plan, reference = adapter_plan(surface, provider_call, adapter_config, reference_paths)
    kwargs = {"credential": credential}
    if surface == "marketing_studio":
        kwargs["catalogue"] = _catalogue(adapter_config)
    return hf.execute_plan(plan, reference, job_dir, **kwargs)


def normalise(results: list) -> ProviderOutcome:
    """Adapter results -> job receipts + one billing record per charge (call or job scope)."""
    jobs, charges, seen = [], [], set()
    for res in results:
        if not isinstance(res, dict):
            raise hf.ProviderRefusal("provider_response_invalid", "result is not an object")
        job_id = str(res.get("id") or "")
        if not job_id:
            raise hf.ProviderRefusal("provider_response_invalid", "result carries no provider job id")
        charge = res.get("charge") if isinstance(res.get("charge"), dict) else {}
        if charge.get("scope") == "call" and charge.get("call_id"):
            ref = f"higgsfield-call:{charge['call_id']}"
        else:
            ref = f"higgsfield-job:{job_id}"
        public = {k: v for k, v in res.items() if k not in ("charge", "result_url")}
        jobs.append({"provider_request_id": job_id if job_id not in {j["provider_request_id"] for j in jobs}
                     else f"{job_id}#{len(jobs)}",
                     "status": "completed" if res.get("status") == "completed" else "failed",
                     "result_url": res.get("result_url"), "raw_sha256": digest(public), "billing_reference": ref})
        if ref in seen:
            continue
        seen.add(ref)
        certainty = charge.get("certainty") or "unknown"
        credits = charge.get("credits")
        note = f"credits={credits if credits is not None else 'null'} certainty={certainty}"
        if charge.get("source"):
            note += f" source={charge['source']}"
        if charge.get("caveat"):
            note += f" caveat={charge['caveat']}"
        if charge.get("fallback_reason"):
            note += f" fallback={charge['fallback_reason']}"
        charges.append({"billing_reference": ref, "certainty": certainty if certainty in (
            "provider_reported", "balance_delta", "estimated", "unknown") else "unknown",
            "credits": None if credits is None else str(credits), "provider_request_id": job_id,
            "raw_sha256": digest(charge or {"absent": True}), "note": note[:2000]})
    outcome = "completed" if jobs and all(j["status"] == "completed" for j in jobs) else "failed"
    return ProviderOutcome(outcome=outcome, jobs=jobs, charges=charges,
                           error=None if outcome == "completed" else error_doc("generation_not_completed",
                                                                               "provider reported a non-completed job"))
