"""SQL calls for CS-16 plans (definer functions, as cs_api inside the caller's actor transaction)."""
from __future__ import annotations

import json
import uuid

from ..errors import ServiceError


def one(cur, sql: str, args: tuple, key: str = "v"):
    cur.execute(sql, args)
    return cur.fetchone()[key]


def lookup(cur, ws, request_object_id, key) -> dict:
    return one(cur, "select public.cs_plan_lookup(%s,%s,%s) as v", (ws, request_object_id, key))


def catalogue(cur) -> list[dict]:
    return one(cur, "select public.cs_plan_catalogue() as v", ())


def save(cur, ws, plan, summary, request, treatment, package_version_id, catalogue_sha, key, tier) -> dict:
    return one(cur, "select public.cs_save_execution_plan(%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s::public.cs_render_tier) as v",
               (ws, json.dumps(plan), json.dumps(summary), request["id"], request["version_id"], treatment["id"],
                treatment["version_id"], package_version_id, catalogue_sha, key, tier))


def get(cur, ws, plan_id) -> dict:
    try:
        plan_id = str(uuid.UUID(str(plan_id)))
    except (ValueError, TypeError):
        raise ServiceError(422, "plan_id_invalid") from None
    return one(cur, "select public.cs_get_execution_plan(%s,%s) as v", (ws, plan_id))
