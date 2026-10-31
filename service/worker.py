"""Creative Studio worker: executes at most one leased work item per call.

The worker claims only stages it has a registered handler for. CS-B1 ships
the scheduling primitive and no stage handlers (stages arrive with CS-B3), so
an unconfigured worker claims nothing and says so; it never marks work done
without a handler result. Handler failures are rescheduled with a readable
failure code (bounded attempts -> dead), never swallowed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from typing import Callable

from .config import ConfigError, Settings
from .db import worker_transaction
from .errors import ServiceError

log = logging.getLogger("creative_studio.worker")


@dataclass
class StageResult:
    result_kind: str
    receipt: dict
    output_refs: list = field(default_factory=list)
    input_sha256: str | None = None

    def receipt_sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class RetryableStageError(Exception):
    def __init__(self, failure_code: str, delay_seconds: int = 30):
        super().__init__(failure_code)
        self.failure_code = failure_code
        self.delay_seconds = delay_seconds


Handler = Callable[[dict, "WorkContext"], StageResult]


@dataclass
class WorkContext:
    settings: Settings
    item: dict

    def heartbeat(self) -> None:
        with worker_transaction(self.settings.worker_database_url) as cur:
            cur.execute("select public.cs_heartbeat_work(%s, %s, %s)",
                        (self.item["work_item_id"], self.item["lease_token"], self.item["fencing_token"]))

    def commit_asset_version(self, *, label: str, kind: str, mime_type: str, data: bytes,
                             width: int | None = None, height: int | None = None, duration_ms: int | None = None,
                             asset_id: str | None = None, source_refs: list | None = None) -> dict:
        """Record an immutable version for bytes the stage has written to Storage."""
        with worker_transaction(self.settings.worker_database_url) as cur:
            cur.execute("select * from public.cs_commit_asset_version(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (self.item["work_item_id"], self.item["lease_token"], self.item["fencing_token"], asset_id,
                         label, kind, mime_type, len(data), hashlib.sha256(data).hexdigest(), width, height,
                         duration_ms, json.dumps(source_refs or []), self.settings.runtime_principal))
            row = dict(cur.fetchone())
        row["sha256"] = hashlib.sha256(data).hexdigest()
        row["workspace_id"] = str(self.item["workspace_id"])
        return row


def run_once(settings: Settings, handlers: dict[str, Handler]) -> dict:
    if not settings.worker_database_url:
        raise ServiceError(503, "worker_database_unconfigured")
    stages = sorted(handlers)
    if not stages:
        return {"claimed": 0, "status": "idle", "reason": "no_stage_handlers_registered"}
    with worker_transaction(settings.worker_database_url) as cur:
        cur.execute("select * from public.cs_claim_work(%s, %s, %s::public.cs_work_stage[], 1)",
                    (settings.environment_id, settings.runtime_principal, stages))
        item = cur.fetchone()
    if item is None:
        return {"claimed": 0, "status": "idle", "reason": "no_due_work"}
    item = dict(item)
    ctx = WorkContext(settings, item)
    lease = (item["work_item_id"], item["lease_token"], item["fencing_token"])
    try:
        result = handlers[item["stage"]](item, ctx)
    except RetryableStageError as err:
        return _reschedule(settings, item, lease, err.failure_code, err.delay_seconds)
    except Exception:
        log.exception("stage handler failed: stage=%s work_item=%s", item["stage"], item["work_item_id"])
        return _reschedule(settings, item, lease, "stage_handler_error", 60)
    with worker_transaction(settings.worker_database_url) as cur:
        cur.execute("select public.cs_finish_work(%s, %s, %s, %s, %s, %s, %s, %s) as stage_result_id",
                    (*lease, result.result_kind, result.input_sha256, json.dumps(result.output_refs),
                     result.receipt_sha256(), settings.release_id))
        stage_result_id = cur.fetchone()["stage_result_id"]
    return {"claimed": 1, "status": "finished", "work_item_id": str(item["work_item_id"]),
            "stage_result_id": str(stage_result_id)}


def _reschedule(settings, item, lease, failure_code, delay) -> dict:
    with worker_transaction(settings.worker_database_url) as cur:
        cur.execute("select public.cs_reschedule_work(%s, %s, %s, %s, %s)::text as state",
                    (*lease, failure_code, delay))
        state = cur.fetchone()["state"]
    return {"claimed": 1, "status": state, "work_item_id": str(item["work_item_id"]), "failure_code": failure_code}


def finalize_upload(settings: Settings, upload_id: str, data: bytes, mime_type: str,
                    width: int | None = None, height: int | None = None, duration_ms: int | None = None) -> dict:
    """Finalize stage body: the caller has fetched the stored object bytes with the
    runtime principal; digest and size are computed here, never taken from a client."""
    with worker_transaction(settings.worker_database_url) as cur:
        cur.execute("select * from public.cs_finalize_asset(%s, %s, %s, %s, %s, %s, %s, %s)",
                    (upload_id, hashlib.sha256(data).hexdigest(), len(data), mime_type, width, height,
                     duration_ms, settings.runtime_principal))
        return dict(cur.fetchone())


# -- HTTP entry (one invocation = at most one item) ----------------------------
HANDLERS: dict[str, Handler] = {}   # CS-B3 registers stage handlers here


def make_wsgi(settings_loader, handlers: dict[str, Handler] | None = None):
    def app(environ, start_response):
        status, payload = 500, {"error": "internal_error"}
        try:
            settings = settings_loader()
            if environ.get("REQUEST_METHOD") != "POST":
                raise ServiceError(405, "method_not_allowed")
            supplied = (environ.get("HTTP_AUTHORIZATION") or "")[len("Bearer "):]
            if not settings.worker_secret:
                raise ServiceError(503, "worker_secret_unconfigured")
            if not supplied or not hmac.compare_digest(supplied.encode(), settings.worker_secret.encode()):
                raise ServiceError(401, "authentication_required")
            status, payload = 200, run_once(settings, HANDLERS if handlers is None else handlers)
        except ServiceError as err:
            status, payload = err.status, err.body()
        except ConfigError as err:
            log.error("worker refused: %s", err)
            status, payload = 503, {"error": "service_unconfigured"}
        except Exception:
            log.exception("worker invocation failed")
        body = json.dumps(payload).encode()
        start_response(f"{status} {'OK' if status == 200 else 'Error'}",
                       [("Content-Type", "application/json"), ("Content-Length", str(len(body))),
                        ("Cache-Control", "no-store")])
        return [body]
    return app
