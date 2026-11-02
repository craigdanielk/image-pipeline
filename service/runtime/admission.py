"""Admission: frozen execution plan -> quote -> account budget lock -> reserve -> admit,
or a typed refusal. The plan is validated against execution-plan-v1 and its
compile_hash / artifact sha256 are recomputed here; SQL then re-checks every
money, catalogue, tier, credential and authority rule inside one transaction.

Until CS-B2 persists plans (cs_compile_plans), the caller supplies the exact
plan document; its version_id is recorded on the run and the admission row.
"""
from __future__ import annotations

import json

import psycopg2

from ..errors import ServiceError
from ..providers.canonical import CanonicalError, artifact_digest, digest
from ..providers.contracts import ContractViolation, validate

PLAN_SCHEMA = "execution-plan-v1.schema.json"


def validate_plan(plan: dict, workspace_id: str) -> dict:
    if not isinstance(plan, dict):
        raise ServiceError(422, "plan_invalid", "plan must be an object")
    try:
        validate(PLAN_SCHEMA, plan)
        compile_hash = digest(plan["program"])
        sha = artifact_digest(plan)
    except ContractViolation as exc:
        raise ServiceError(422, "plan_invalid", "; ".join(exc.errors[:3])) from None
    except CanonicalError as exc:
        raise ServiceError(422, "plan_invalid", str(exc)) from None
    if plan["workspace_id"] != workspace_id:
        raise ServiceError(404, "resource_unavailable")
    if compile_hash != plan["compile_hash"]:
        raise ServiceError(422, "plan_invalid", "compile_hash does not match the canonical program")
    if sha != plan["sha256"]:
        raise ServiceError(422, "plan_invalid", "sha256 does not match the canonical plan")
    return {"compile_hash": compile_hash, "sha256": sha, "version_id": plan["version_id"],
            "budget_ceiling_micros": plan["program"]["budget_ceiling_micros"], "nodes": plan_nodes(plan)}


def plan_nodes(plan: dict) -> list[dict]:
    return [{"node_id": n["id"], "dependencies": n["dependencies"], "inputs": n["inputs"],
             "provider_call": n["provider_call"], "estimated_cost_micros": n["estimated_cost_micros"],
             "maximum_cost_micros": n["maximum_cost_micros"]} for n in plan["program"]["nodes"]]


def _tier(body: dict) -> str:
    tier = body.get("tier")
    if tier not in ("draft", "final"):
        raise ServiceError(422, "tier_required", "tier must be draft or final")
    return tier


def quote(cur, workspace_id: str, body: dict) -> dict:
    checked = validate_plan(body.get("plan"), workspace_id)
    cur.execute("select public.cs_quote_run(%s, %s::jsonb, %s::public.cs_render_tier) as q",
                (workspace_id, json.dumps(checked["nodes"]), _tier(body)))
    return cur.fetchone()["q"]


def admit(cur, settings, workspace_id: str, body: dict, idempotency_key: str, request_sha256: str) -> dict:
    checked = validate_plan(body.get("plan"), workspace_id)
    approval = body.get("draft_approval_id")
    try:
        cur.execute("""select * from public.cs_admit_run(%s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                                                         %s::public.cs_render_tier, %s, %s, %s)""",
                    (workspace_id, settings.environment_id, settings.release_id, checked["version_id"],
                     checked["sha256"], checked["compile_hash"], checked["budget_ceiling_micros"],
                     json.dumps(checked["nodes"]), _tier(body), approval, idempotency_key, request_sha256))
    except psycopg2.Error as exc:
        diag = getattr(exc, "diag", None)
        message = (getattr(diag, "message_primary", None) or "")
        if message.startswith("provider_credentials_missing") and "no active workspace credential" in message:
            # CS-19: the workspace-funded path has no workspace key -> plain, typed BYOK refusal.
            raise ServiceError(403, "byok_required", "add your Higgsfield key in Integrations") from None
        raise
    return dict(cur.fetchone())
