#!/usr/bin/env python3
"""ENGINE adapter: higgsfield_marketing — Higgsfield Marketing Studio (DTC Ads Engine).

Sits behind the existing provider seam (spec -> ProviderPrompt ->
ProviderExecutionPlan -> adapters). Exposes:

  brand_kit_upsert(payload)            ours feeds theirs (Brand CI export payload)
  dtc_ad_generate(format_id, ...)      branded ad image, quality low|medium|high
  ad_formats()                         format presets from the catalogue file
  cost_estimate(format_id, ...)        credit estimate without spend
  execute_plan(plan, reference, ...)   ProviderExecutionPlan entry point

Rules this module enforces (and its tests prove):

* No machine CLI session at runtime. The adapter never spawns the `higgsfield`
  binary and never reads a local login. Every call needs a *workspace API
  credential* resolved by the caller; none -> typed refusal
  `provider_credentials_missing`.
* No network of its own. All provider traffic goes through an injected
  `transport` (the hosted API transport is CS-M3 work). No transport -> typed
  refusal `provider_transport_unavailable`.
* Brand kits are pushed from our approved Brand CI export, never derived from
  `brand-kits fetch --url`. A URL-only payload -> `brand_kit_url_source_forbidden`.
  (CLI 1.1.26 `brand-kits` exposes only fetch/get/list; create-from-payload is an
  API operation and is unverified until the hosted transport exists.)
* Model/format ids, quality->tier mapping and limits come from a catalogue file,
  never from literals here. Quality low/medium = draft tier, high = final tier,
  as declared by the catalogue.
* Credits -> currency only with a caller-supplied, sourced unit price; otherwise
  the currency value is `None` with an explicit missing reason.

The parameter shape sent to the transport mirrors the CLI `--from-file` params
shape of `marketing-studio dtc-ads generate` (prompt, format_id, brand_kit_id,
product, media[{id,role}], quality, resolution, aspect_ratio, batch_size).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional, Protocol

PROVIDER_ID = "higgsfield_marketing"
CATALOGUE_ENV = "CS_HIGGSFIELD_MARKETING_CATALOGUE"
CATALOGUE_SCHEMA = "cs-higgsfield-marketing-catalogue-v1"

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


# --------------------------------------------------------------------- refusals

class ProviderRefusal(Exception):
    """Typed, user-readable refusal. `code` is stable; `detail` is for humans."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail

    def to_dict(self) -> dict:
        return {"provider": PROVIDER_ID, "refusal": self.code, "detail": self.detail}


# ------------------------------------------------------------------ credentials

@dataclass(frozen=True)
class WorkspaceCredential:
    """A workspace-scoped provider API credential resolved by the caller.

    `secret_ref` is an opaque handle the transport dereferences server-side; the
    adapter never sees or logs secret material. `credit_unit_price_usd` is the
    workspace's contracted price per credit and must carry `credit_price_source`.
    """

    workspace_id: str
    credential_id: str
    secret_ref: str
    mode: str = "workspace_api"
    credit_unit_price_usd: Optional[Decimal] = None
    credit_price_source: Optional[str] = None


def require_credential(credential: Optional[WorkspaceCredential]) -> WorkspaceCredential:
    if credential is None:
        raise ProviderRefusal(
            "provider_credentials_missing",
            "Connect a workspace Higgsfield API credential; machine CLI logins are not a runtime path.",
        )
    if not isinstance(credential, WorkspaceCredential):
        raise ProviderRefusal("provider_credentials_missing", "Credential must be a resolved workspace credential.")
    if credential.mode != "workspace_api":
        raise ProviderRefusal(
            "provider_credentials_missing",
            f"Credential mode '{credential.mode}' is not allowed; only workspace_api credentials run.",
        )
    if not (credential.workspace_id and credential.credential_id and credential.secret_ref):
        raise ProviderRefusal("provider_credentials_missing", "Credential is incomplete.")
    return credential


# -------------------------------------------------------------------- transport

class Transport(Protocol):
    def request(self, operation: str, params: dict, credential: WorkspaceCredential) -> Any: ...


def require_transport(transport: Optional[Transport]) -> Transport:
    if transport is None or not callable(getattr(transport, "request", None)):
        raise ProviderRefusal(
            "provider_transport_unavailable",
            "No hosted Marketing Studio API transport is configured for this deployment.",
        )
    return transport


# -------------------------------------------------------------------- catalogue

@dataclass(frozen=True)
class Catalogue:
    raw: dict
    sha256: str
    quality_tiers: dict = field(default_factory=dict)
    formats: dict = field(default_factory=dict)
    aspect_ratios: tuple = ()
    resolutions: tuple = ()
    references_max: int = 0
    batch_size_max: int = 0
    default_quality: dict = field(default_factory=dict)

    def tier_for(self, quality: str) -> str:
        if quality not in self.quality_tiers:
            raise ProviderRefusal("quality_unknown", f"Quality '{quality}' is not in the catalogue.")
        return self.quality_tiers[quality]

    def format(self, format_id: str) -> dict:
        if format_id not in self.formats:
            raise ProviderRefusal("ad_format_unknown", f"Ad format '{format_id}' is not in the catalogue.")
        return self.formats[format_id]


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def load_catalogue(path: Optional[str] = None) -> Catalogue:
    path = path or os.environ.get(CATALOGUE_ENV)
    if not path:
        raise ProviderRefusal("provider_catalogue_missing", f"Set {CATALOGUE_ENV} or pass a catalogue path.")
    if not os.path.isfile(path):
        raise ProviderRefusal("provider_catalogue_missing", f"Catalogue file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        try:
            raw = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ProviderRefusal("provider_catalogue_invalid", f"Catalogue is not JSON: {exc}") from exc
    return parse_catalogue(raw)


def parse_catalogue(raw: dict) -> Catalogue:
    def bad(msg: str) -> ProviderRefusal:
        return ProviderRefusal("provider_catalogue_invalid", msg)

    if not isinstance(raw, dict) or raw.get("schema_version") != CATALOGUE_SCHEMA:
        raise bad(f"schema_version must be {CATALOGUE_SCHEMA}")
    if raw.get("provider") != PROVIDER_ID:
        raise bad(f"provider must be {PROVIDER_ID}")
    tiers = raw.get("quality_tiers")
    if not isinstance(tiers, dict) or not tiers or any(t not in ("draft", "final") for t in tiers.values()):
        raise bad("quality_tiers must map quality -> draft|final")
    if "final" not in tiers.values() or "draft" not in tiers.values():
        raise bad("quality_tiers must declare at least one draft and one final quality")
    formats = raw.get("ad_formats")
    if not isinstance(formats, list) or not formats:
        raise bad("ad_formats must be a non-empty list")
    by_id = {}
    for f in formats:
        if not isinstance(f, dict) or not _UUID.match(str(f.get("id", ""))) or not f.get("type"):
            raise bad("each ad format needs a uuid id and a type")
        by_id[f["id"]] = f
    limits = raw.get("limits") or {}
    for key in ("references_max", "batch_size_max"):
        if not isinstance(limits.get(key), int) or limits[key] < 1:
            raise bad(f"limits.{key} must be a positive integer")
        if not (limits.get("source") or {}).get(key):
            raise bad(f"limits.source.{key} must cite where the limit comes from")
    defaults = raw.get("tier_defaults") or {}
    for tier in ("draft", "final"):
        q = defaults.get(tier)
        if q not in tiers or tiers[q] != tier:
            raise bad(f"tier_defaults.{tier} must name a quality mapped to {tier}")
    return Catalogue(
        raw=raw,
        sha256=_sha256(raw),
        quality_tiers=dict(tiers),
        formats=by_id,
        aspect_ratios=tuple(raw.get("aspect_ratios") or ()),
        resolutions=tuple(raw.get("resolutions") or ()),
        references_max=limits["references_max"],
        batch_size_max=limits["batch_size_max"],
        default_quality=dict(defaults),
    )


def _resolve_catalogue(catalogue: Optional[Catalogue | str]) -> Catalogue:
    if isinstance(catalogue, Catalogue):
        return catalogue
    return load_catalogue(catalogue)


# ------------------------------------------------------------------- brand kit

_BRAND_KIT_FIELDS = ("name", "tagline", "colours", "fonts", "logo_url", "tone", "values")


def validate_brand_kit_payload(payload: dict) -> dict:
    """Validate the Brand CI (ENG-03b) export payload and return a normalised copy."""
    if not isinstance(payload, dict):
        raise ProviderRefusal("brand_kit_payload_invalid", "Brand kit payload must be an object.")
    url_keys = {"url", "source_url", "website", "fetch_url"} & set(payload)
    if url_keys:
        raise ProviderRefusal(
            "brand_kit_url_source_forbidden",
            f"Brand kits are pushed from the approved Brand CI export, not fetched from a website "
            f"(remove {sorted(url_keys)}).",
        )
    missing = [k for k in _BRAND_KIT_FIELDS if k not in payload]
    if missing:
        raise ProviderRefusal("brand_kit_payload_invalid", f"Missing fields: {', '.join(missing)}")
    name = payload["name"]
    if not isinstance(name, str) or not name.strip():
        raise ProviderRefusal("brand_kit_payload_invalid", "name must be a non-empty string")
    if payload["tagline"] is not None and not isinstance(payload["tagline"], str):
        raise ProviderRefusal("brand_kit_payload_invalid", "tagline must be a string or null")
    colours = payload["colours"]
    if not isinstance(colours, list) or not colours:
        raise ProviderRefusal("brand_kit_payload_invalid", "colours must be a non-empty list")
    for c in colours:
        if not isinstance(c, dict) or not _HEX.match(str(c.get("hex", ""))) or not c.get("role"):
            raise ProviderRefusal("brand_kit_payload_invalid", "each colour needs hex (#RRGGBB) and role")
    fonts = payload["fonts"]
    if not isinstance(fonts, list) or not fonts:
        raise ProviderRefusal("brand_kit_payload_invalid", "fonts must be a non-empty list")
    for f in fonts:
        if not isinstance(f, dict) or not f.get("family") or not f.get("role"):
            raise ProviderRefusal("brand_kit_payload_invalid", "each font needs family and role")
    logo = payload["logo_url"]
    if not isinstance(logo, str) or not logo.startswith("https://"):
        raise ProviderRefusal("brand_kit_payload_invalid", "logo_url must be an https URL to the stored logo file")
    tone = payload["tone"]
    if not (isinstance(tone, str) and tone.strip()) and not (isinstance(tone, list) and tone and all(isinstance(t, str) for t in tone)):
        raise ProviderRefusal("brand_kit_payload_invalid", "tone must be a string or list of strings")
    values = payload["values"]
    if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
        raise ProviderRefusal("brand_kit_payload_invalid", "values must be a list of strings")
    normalised = {k: payload[k] for k in _BRAND_KIT_FIELDS}
    if payload.get("source_version"):
        normalised["source_version"] = payload["source_version"]
    return normalised


def brand_kit_upsert(payload: dict, *, credential: Optional[WorkspaceCredential] = None,
                     transport: Optional[Transport] = None) -> dict:
    """Push our approved brand kit to Marketing Studio. Idempotent on payload hash."""
    body = validate_brand_kit_payload(payload)
    cred = require_credential(credential)
    tx = require_transport(transport)
    payload_sha = _sha256(body)
    res = tx.request("brand_kits.upsert", {"brand_kit": body, "idempotency_key": payload_sha}, cred)
    kit = res.get("brand_kit", res) if isinstance(res, dict) else None
    kit_id = (kit or {}).get("id")
    if not isinstance(kit_id, str) or not _UUID.match(kit_id):
        raise ProviderRefusal("provider_response_invalid", "brand kit upsert returned no id")
    return {"provider": PROVIDER_ID, "brand_kit_id": kit_id, "payload_sha256": payload_sha,
            "workspace_id": cred.workspace_id, "credential_id": cred.credential_id}


# -------------------------------------------------------------------- ad params

def ad_formats(catalogue: Optional[Catalogue | str] = None, *, type: Optional[str] = None) -> list[dict]:
    cat = _resolve_catalogue(catalogue)
    out = [dict(f) for f in cat.formats.values()]
    if type:
        out = [f for f in out if f.get("type") == type]
    return out


def _media_list(references: Optional[list], cat: Catalogue) -> list[dict]:
    refs = list(references or [])
    if len(refs) > cat.references_max:
        raise ProviderRefusal(
            "references_exceed_limit", f"{len(refs)} references; the catalogue limit is {cat.references_max}."
        )
    media = []
    for r in refs:
        if isinstance(r, str):
            r = {"id": r, "role": "image"}
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not r["id"]:
            raise ProviderRefusal("reference_invalid", "references must be provider media ids or {id, role}")
        if os.path.sep in r["id"] or r["id"].startswith(("http://", "https://")):
            raise ProviderRefusal(
                "reference_upload_required",
                "References must already be provider media inputs; uploads happen in the runtime, not this adapter.",
            )
        media.append({"id": r["id"], "role": r.get("role") or "image"})
    return media


def build_params(*, format_id: str, prompt: str, brand_kit_id: Optional[str], product: Optional[str],
                 references: Optional[list], quality: str, aspect_ratio: Optional[str] = None,
                 resolution: Optional[str] = None, batch_size: int = 1,
                 catalogue: Optional[Catalogue | str] = None) -> tuple[dict, dict]:
    """Validate against the catalogue and return (params, meta)."""
    cat = _resolve_catalogue(catalogue)
    fmt = cat.format(format_id)
    tier = cat.tier_for(quality)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ProviderRefusal("prompt_missing", "The compiled ProviderPrompt is required.")
    if brand_kit_id is not None and not _UUID.match(str(brand_kit_id)):
        raise ProviderRefusal("brand_kit_id_invalid", "brand_kit_id must be a provider uuid")
    if aspect_ratio is not None and cat.aspect_ratios and aspect_ratio not in cat.aspect_ratios:
        raise ProviderRefusal("aspect_ratio_unsupported", f"{aspect_ratio} is not in the catalogue.")
    if resolution is not None and cat.resolutions and resolution not in cat.resolutions:
        raise ProviderRefusal("resolution_unsupported", f"{resolution} is not in the catalogue.")
    if not isinstance(batch_size, int) or batch_size < 1 or batch_size > cat.batch_size_max:
        raise ProviderRefusal("batch_size_invalid", f"batch_size must be 1..{cat.batch_size_max}")
    params: dict[str, Any] = {
        "prompt": prompt,
        "format_id": format_id,
        "quality": quality,
        "batch_size": batch_size,
        "media": _media_list(references, cat),
    }
    if brand_kit_id:
        params["brand_kit_id"] = brand_kit_id
    if product:
        params["product"] = [product]
    if aspect_ratio:
        params["aspect_ratio"] = aspect_ratio
    if resolution:
        params["resolution"] = resolution
    meta = {"tier": tier, "format": fmt, "catalogue_sha256": cat.sha256,
            "request_sha256": _sha256(params)}
    return params, meta


def _credits_from(res: Any) -> Decimal:
    node = res
    if isinstance(node, dict) and isinstance(node.get("cost"), dict):
        node = node["cost"]
    val = node.get("credits") if isinstance(node, dict) else None
    if isinstance(val, bool) or not isinstance(val, (int, float, str)):
        raise ProviderRefusal("provider_response_invalid", "cost estimate returned no credits")
    try:
        credits = Decimal(str(val))
    except Exception as exc:  # decimal.InvalidOperation
        raise ProviderRefusal("provider_response_invalid", f"credits not numeric: {val!r}") from exc
    if credits < 0:
        raise ProviderRefusal("provider_response_invalid", "credits negative")
    return credits


def _price(credits: Decimal, cred: WorkspaceCredential) -> dict:
    if cred.credit_unit_price_usd is None or not cred.credit_price_source:
        return {"usd": None, "usd_status": "missing",
                "missing_reason": "credit_price_unknown: workspace credential carries no sourced credit unit price"}
    usd = (credits * Decimal(str(cred.credit_unit_price_usd))).quantize(Decimal("0.000001"))
    return {"usd": str(usd), "usd_status": "estimated", "price_source": cred.credit_price_source}


def cost_estimate(format_id: str, prompt: str, *, brand_kit_id: Optional[str] = None,
                  product: Optional[str] = None, references: Optional[list] = None,
                  quality: Optional[str] = None, tier: Optional[str] = None,
                  aspect_ratio: Optional[str] = None, resolution: Optional[str] = None,
                  batch_size: int = 1, credential: Optional[WorkspaceCredential] = None,
                  transport: Optional[Transport] = None,
                  catalogue: Optional[Catalogue | str] = None) -> dict:
    """Credit estimate via `dtc-ads generate --cost-only` semantics. No job is created."""
    cat = _resolve_catalogue(catalogue)
    quality = quality or (cat.default_quality.get(tier) if tier else None)
    if not quality:
        raise ProviderRefusal("quality_unknown", "Pass quality or a tier with a catalogue default.")
    params, meta = build_params(format_id=format_id, prompt=prompt, brand_kit_id=brand_kit_id, product=product,
                                references=references, quality=quality, aspect_ratio=aspect_ratio,
                                resolution=resolution, batch_size=batch_size, catalogue=cat)
    cred = require_credential(credential)
    tx = require_transport(transport)
    res = tx.request("dtc_ads.generate", {**params, "cost_only": True}, cred)
    credits = _credits_from(res)
    return {"provider": PROVIDER_ID, "credits": str(credits), "quality": quality, "tier": meta["tier"],
            "request_sha256": meta["request_sha256"], "catalogue_sha256": meta["catalogue_sha256"],
            **_price(credits, cred)}


def dtc_ad_generate(format_id: str, brand_kit_id: Optional[str], product: Optional[str],
                    references: Optional[list], quality: str, *, prompt: str,
                    aspect_ratio: Optional[str] = None, resolution: Optional[str] = None,
                    batch_size: int = 1, credential: Optional[WorkspaceCredential] = None,
                    transport: Optional[Transport] = None,
                    catalogue: Optional[Catalogue | str] = None) -> list[dict]:
    """Generate branded ad image(s) and block until complete. Returns normalised results."""
    params, meta = build_params(format_id=format_id, prompt=prompt, brand_kit_id=brand_kit_id, product=product,
                                references=references, quality=quality, aspect_ratio=aspect_ratio,
                                resolution=resolution, batch_size=batch_size, catalogue=catalogue)
    cred = require_credential(credential)
    tx = require_transport(transport)
    res = tx.request("dtc_ads.generate", {**params, "wait": True}, cred)
    jobs = res.get("jobs") if isinstance(res, dict) and isinstance(res.get("jobs"), list) else res
    jobs = jobs if isinstance(jobs, list) else [jobs]
    out = []
    for job in jobs:
        if not isinstance(job, dict):
            raise ProviderRefusal("provider_response_invalid", "generation returned a non-object job")
        results = job.get("results") if isinstance(job.get("results"), list) else None
        urls = [r.get("url") for r in results if isinstance(r, dict)] if results else [job.get("result_url")]
        if job.get("status") != "completed" or not urls or not all(isinstance(u, str) and u.startswith("https://") for u in urls):
            raise ProviderRefusal("generation_not_completed",
                                  f"job {job.get('id')} status={job.get('status')!r}")
        for u in urls:
            out.append({
                "provider": PROVIDER_ID, "id": job.get("id"), "status": "completed", "result_url": u,
                "tier": meta["tier"], "quality": quality, "format_id": format_id,
                "brand_kit_id": brand_kit_id, "reference_media_ids": [m["id"] for m in params["media"]],
                "request_sha256": meta["request_sha256"], "catalogue_sha256": meta["catalogue_sha256"],
                "text_model_drawn": True,
            })
    if len(out) != params["batch_size"]:
        raise ProviderRefusal("provider_response_invalid",
                              f"expected {params['batch_size']} results, got {len(out)}")
    return out


# --------------------------------------------------------------- provider seam

def _plan_args(plan: dict, reference: Any) -> dict:
    if plan.get("provider") != PROVIDER_ID:
        raise ProviderRefusal("plan_provider_mismatch", f"plan.provider must be {PROVIDER_ID}")
    params = dict(plan.get("params") or {})
    output = plan.get("output") or {}
    refs = list(plan.get("media_inputs") or [])
    if reference:
        refs += list(reference if isinstance(reference, list) else [reference])
    prompt = ((plan.get("prompt") or {}).get("positive")) or ""
    return {
        "format_id": params.get("format_id") or plan.get("model"),
        "prompt": prompt,
        "brand_kit_id": params.get("brand_kit_id"),
        "product": params.get("product"),
        "references": refs,
        "quality": params.get("quality"),
        "aspect_ratio": output.get("aspect_ratio") or params.get("aspect_ratio"),
        "resolution": output.get("resolution") or params.get("resolution"),
        "batch_size": int(output.get("count") or params.get("batch_size") or 1),
    }


def execute_plan(plan: dict, reference: Any = None, job_dir: str = ".", dry_run: bool = False, *,
                 credential: Optional[WorkspaceCredential] = None, transport: Optional[Transport] = None,
                 catalogue: Optional[Catalogue | str] = None):
    """Execute a ProviderExecutionPlan (provider=higgsfield_marketing).

    Signature-compatible with engine_higgsfield.execute_plan; credential, transport
    and catalogue are keyword-only and supplied by the hosted runtime. Called
    without them (e.g. from the local graph runtime) it refuses with a typed reason.
    """
    a = _plan_args(plan, reference)
    if not a["quality"]:
        raise ProviderRefusal("quality_unknown", "plan.params.quality is required")
    if dry_run or plan.get("method") == "cost_estimate":
        return cost_estimate(a["format_id"], a["prompt"], brand_kit_id=a["brand_kit_id"], product=a["product"],
                             references=a["references"], quality=a["quality"], aspect_ratio=a["aspect_ratio"],
                             resolution=a["resolution"], batch_size=a["batch_size"], credential=credential,
                             transport=transport, catalogue=catalogue)
    return dtc_ad_generate(a["format_id"], a["brand_kit_id"], a["product"], a["references"], a["quality"],
                           prompt=a["prompt"], aspect_ratio=a["aspect_ratio"], resolution=a["resolution"],
                           batch_size=a["batch_size"], credential=credential, transport=transport,
                           catalogue=catalogue)


# Legacy seam names (cost/generate) so `get_engine("higgsfield_marketing")` callers that
# use the frozen engine signature get a typed refusal instead of an AttributeError.
def cost(prompt, output, engine, job_dir="."):
    raise ProviderRefusal("provider_credentials_missing",
                          "The JobSpec engine seam carries no workspace credential; use execute_plan from the hosted runtime.")


def generate(prompt, reference, output, engine, job_dir="."):
    raise ProviderRefusal("provider_credentials_missing",
                          "The JobSpec engine seam carries no workspace credential; use execute_plan from the hosted runtime.")

