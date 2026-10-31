"""Quote and admission from a persisted plan (CS-16). Spend stays behind the existing B3 admission + the
paid_provider_enabled switch; nothing here calls a provider.

Price source: the catalogue entry's price comes from the single adapter's own dry-run cost call (made when the
catalogue is published, validated against the cited credit price). A quote here re-prices the pinned plan nodes from
the CURRENT catalogue via cs_quote_run, so a changed/expired price refuses (plan_stale / price_unknown) instead of
inventing one. A missing provider credential is `unavailable: provider_credentials_missing`, never a made-up number."""
from __future__ import annotations

import json
from decimal import Decimal

from ..errors import ServiceError
from ..requests import store as rstore
from . import store

CERTAINTY = "catalogue_dry_run_snapshot"


def _load_current(cur, ws: str, plan_id) -> dict:
    row = store.get(cur, ws, plan_id)
    if row["superseded_by"]:
        raise ServiceError(409, "plan_stale", f"superseded by {row['superseded_by']}")
    head_r = rstore.get_version(cur, "cs_creative_request_versions", ws, row["request_object_id"])
    head_t = rstore.get_version(cur, "cs_treatment_versions", ws, row["treatment_object_id"])
    if (head_r is None or head_t is None or head_r["payload"]["version_id"] != row["request_version_id"]
            or head_t["payload"]["version_id"] != row["treatment_version_id"]):
        raise ServiceError(409, "plan_stale", "the request or treatment changed after this plan was compiled")
    return row


def _tier(body: dict) -> str:
    tier = body.get("tier")
    if tier not in ("draft", "final"):
        raise ServiceError(422, "tier_required", "tier must be draft or final")
    return tier


def quote_plan(cur, settings, ws: str, body: dict, quote_fn) -> dict:
    row = _load_current(cur, ws, body["plan_id"])
    plan, tier = row["payload"], _tier(body)
    mode = plan["admission"]["budget"]["funding_source"]
    providers = sorted({n["provider_call"]["provider"] for n in plan["program"]["nodes"] if n["provider_call"]})
    base = {"plan_id": plan["id"], "plan_sha256": plan["sha256"], "tier": tier}
    for provider in providers:
        cur.execute("select public.cs_plan_credential_available(%s,%s,%s,%s) as ok",
                    (ws, settings.environment_id, provider, mode))
        if not cur.fetchone()["ok"]:
            return dict(base, status="unavailable", reason="provider_credentials_missing", credits=None, usd=None,
                        certainty=None)
    q = quote_fn(cur, ws, {"plan": plan, "tier": tier})
    credits = Decimal(0)
    for n in q["nodes"]:
        per_unit = (n.get("price_snapshot") or {}).get("quoted_credits")
        if per_unit is None:
            return dict(base, status="unavailable", reason="price_unknown", credits=None, usd=None, certainty=None)
        credits += Decimal(str(per_unit)) * int(n["units"])
    result = dict(base, status="quoted", credits=format(credits.normalize(), "f"),
                  usd=format(Decimal(q["estimated_micros"]) / Decimal(1_000_000), "f"),
                  estimated_micros=q["estimated_micros"], maximum_micros=q["maximum_micros"], certainty=CERTAINTY,
                  budget=q.get("budget"),
                  nodes=[{k: n[k] for k in ("node_id", "entry_id", "units", "quote_micros")} for n in q["nodes"]])
    cur.execute("select public.cs_record_plan_quote(%s,%s,%s::public.cs_render_tier,%s::jsonb)",
                (ws, plan["id"], tier, json.dumps(result)))
    return result


def plan_for_admission(cur, settings, ws: str, body: dict) -> dict:
    """Return the body to hand to the existing admission: the stored plan, only if a quote exists for the tier."""
    row = _load_current(cur, ws, body["plan_id"])
    tier = _tier(body)
    cur.execute("select public.cs_latest_plan_quote(%s,%s,%s::public.cs_render_tier) as q",
                (ws, row["payload"]["id"], tier))
    q = cur.fetchone()["q"]
    if q is None or q.get("status") != "quoted":
        raise ServiceError(409, "quote_required", "quote this plan for the tier before admitting a run")
    cur.execute("select public.cs_paid_provider_enabled(%s) as on", (settings.environment_id,))
    if not cur.fetchone()["on"]:
        raise ServiceError(503, "paid_provider_disabled", "paid provider work is switched off for this environment")
    return {k: v for k, v in body.items() if k != "plan_id"} | {"plan": row["payload"]}
