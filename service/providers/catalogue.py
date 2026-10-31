"""Provider catalogue from data.

Entries come from an operator source file (provider surfaces, transport,
limits with citations, the credit price with its source) and their prices
come from the adapter's own cost quote (`generate cost` / `dtc-ads
--cost-only`) x the cited credit price. No price literal lives in code. An
entry whose quote cannot be read is not published (reported as price_unknown);
admission then refuses it with `price_unknown`.

Each published entry is a provider-catalogue-v1 entry validated against the
contract schema, stored as an immutable cs_provider_catalogue_versions row.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .adapter import engine_higgsfield as hf

from . import higgsfield
from .canonical import digest
from .contracts import validate_subschema

SOURCE_SCHEMA = "cs-b3-catalogue-source-v1"


class CatalogueSourceError(ValueError):
    pass


@dataclass(frozen=True)
class CatalogueEntry:
    entry_id: str
    model_id: str
    endpoint_key: str
    surface: str
    operation: str
    tier: str
    credential_modes: list
    entry: dict
    adapter_config: dict
    price_snapshot: dict
    unit_cost_micros: int
    max_units: int
    valid_until: str
    price_expires_at: str
    sha256: str


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _require(source: dict, *keys):
    node = source
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            raise CatalogueSourceError(f"catalogue source missing {'.'.join(keys)}")
        node = node[k]
    return node


def _entry_doc(source, *, entry_id, model_id, endpoint_key, operation, surface_cfg, unit_cost, max_units, observed_at):
    t = source["transport"]
    price = source["credit_price"]
    request_schema = surface_cfg["request_schema"]
    return {
        "id": entry_id, "provider": source["provider"], "model_id": model_id,
        "adapter_version": higgsfield.ADAPTER_REVISION, "operation": operation, "endpoint_key": endpoint_key,
        "enabled": True, "verified_at": observed_at, "valid_until": source["valid_until"],
        "documentation_url": t["documentation_url"], "submit": t["submit"], "request_schema": request_schema,
        "request_schema_sha256": digest(request_schema), "reference_roles": surface_cfg["reference_roles"],
        "outputs": surface_cfg["outputs"], "controls": surface_cfg["controls"], "idempotency": t["idempotency"],
        "status": t["status"],
        "pricing": {"currency": "USD", "unit": "image", "unit_cost_micros": unit_cost,
                    "maximum_request_cost_micros": unit_cost * max_units, "source_url": price["source_url"],
                    "observed_at": price["observed_at"], "expires_at": price["expires_at"]},
        "credential_modes": source["credential_modes"],
    }


def build_entries(source: dict, *, credential, allow_fixture: bool = False) -> tuple[list[CatalogueEntry], list[dict]]:
    """Return (entries, refusals). Quotes each surface through the adapter (no job is created)."""
    if source.get("schema_version") != SOURCE_SCHEMA:
        raise CatalogueSourceError(f"schema_version must be {SOURCE_SCHEMA}")
    if source.get("provider") != hf.PROVIDER_ID:
        raise CatalogueSourceError("only the higgsfield adapter is wired in this runtime")
    kind = _require(source, "provenance", "kind")
    if kind == "fixture" and not allow_fixture:
        raise CatalogueSourceError("refusing to publish a fixture catalogue source outside tests")
    price = _require(source, "credit_price")
    credit_price = price.get("credit_price_micros")
    if not isinstance(credit_price, int) or credit_price <= 0 or not price.get("source_url"):
        raise CatalogueSourceError("credit_price.credit_price_micros (positive int) and source_url are required")
    for k in ("transport", "valid_until", "credential_modes", "probe_prompt"):
        _require(source, k)
    observed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    probe = source["probe_prompt"]
    snapshot_base = {"credit_price_micros": credit_price, "source_url": price["source_url"],
                     "observed_at": price["observed_at"], "expires_at": price["expires_at"],
                     "provenance": source["provenance"]}
    entries, refusals = [], []

    def add(entry_id, model_id, endpoint_key, surface, operation, tier, parameters, surface_cfg, adapter_config,
            max_units, max_units_source):
        try:
            credits = higgsfield.quote_credits(surface, model_id, parameters, probe, adapter_config, credential)
        except Exception as exc:   # recorded per entry; the entry is not published
            refusals.append({"entry_id": entry_id, "refusal": "price_unknown",
                             "detail": f"{type(exc).__name__}: {str(exc)[:200]}"})
            return
        if credits <= 0:
            refusals.append({"entry_id": entry_id, "refusal": "price_unknown", "detail": "quote returned no credits"})
            return
        unit = higgsfield.micros_for_credits(credits, credit_price)
        doc = _entry_doc(source, entry_id=entry_id, model_id=model_id, endpoint_key=endpoint_key, operation=operation,
                         surface_cfg=surface_cfg, unit_cost=unit, max_units=max_units, observed_at=observed_at)
        validate_subschema("provider-catalogue-v1.schema.json", "/properties/entries/items", doc)
        snapshot = dict(snapshot_base, quote_source=f"{hf.PROVIDER_ID} adapter cost quote ({surface})",
                        quoted_credits=str(credits), quote_parameters=parameters, quoted_at=observed_at,
                        max_units_source=max_units_source)
        sha = digest({"entry": doc, "adapter_config": adapter_config, "price_snapshot": snapshot,
                      "tier": tier, "surface": surface})
        entries.append(CatalogueEntry(entry_id, model_id, endpoint_key, surface, operation, tier,
                                      list(source["credential_modes"]), doc, adapter_config, snapshot, unit, max_units,
                                      source["valid_until"], price["expires_at"], sha))

    ms = source.get("marketing_studio")
    if ms:
        cat = hf.parse_catalogue(ms["catalogue"])
        formats = ms.get("formats") or sorted(cat.formats)
        adapter_config = {"surface": "marketing_studio", "catalogue": ms["catalogue"],
                          "controls": ms["controls"], "result_origins": source["transport"]["result_origins"]}
        for format_id in formats:
            fmt = cat.format(format_id)
            for quality in ms["qualities"]:
                tier = cat.tier_for(quality)
                entry_id = f"hf-ms-{_slug(fmt['type'])}-{format_id[:8]}-{_slug(quality)}"
                add(entry_id, format_id, "marketing-studio-dtc-ads", "marketing_studio", "image_generate", tier,
                    {"quality": quality, "format_id": format_id, "batch_size": 1}, ms, adapter_config,
                    cat.batch_size_max, ms["catalogue"]["limits"]["source"]["batch_size_max"])
    for model in source.get("models") or []:
        for k in ("entry_id", "model_id", "tier", "operation", "max_units", "max_units_source", "probe_parameters"):
            if k not in model:
                raise CatalogueSourceError(f"model entry missing {k}")
        adapter_config = {"surface": "generate", "controls": model["controls"],
                          "result_origins": source["transport"]["result_origins"]}
        add(model["entry_id"], model["model_id"], model.get("endpoint_key", "generate"), "generate",
            model["operation"], model["tier"], dict(model["probe_parameters"]), model, adapter_config,
            int(model["max_units"]), model["max_units_source"])
    return entries, refusals


def publish(cur, entries: list[CatalogueEntry]) -> list[str]:
    """Operator/deployment session only (no runtime role can execute this procedure)."""
    import json
    ids = []
    for e in entries:
        cur.execute("""select public.cs_publish_catalogue_entry(%s, %s, %s, %s, %s, %s, %s::public.cs_render_tier, %s,
                         %s::public.cs_credential_mode[], %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s)""",
                    (hf.PROVIDER_ID, e.entry_id, e.model_id, e.endpoint_key, e.surface, e.operation, e.tier, True,
                     e.credential_modes, json.dumps(e.entry), json.dumps(e.adapter_config),
                     json.dumps(e.price_snapshot), e.unit_cost_micros, e.max_units, e.valid_until,
                     e.price_expires_at, e.sha256))
        ids.append(str(cur.fetchone()[0]))
    return ids
