"""CS-B3 provider stages on CS-B1's durable work queue.

One invocation = recovery sweep + at most one leased item:

  provider_submit  prepare attempt (immutable, resolved node-input hash)
                   -> resolve references + credential (retryable, pre-dispatch)
                   -> write frozen dispatch envelope (dispatch marker; checks
                      cancel, current eligibility, credential, reservation)
                   -> ONE adapter call -> receipts + settlement + enqueue ingest
  ingest_original  fetch provider outputs from allowed origins -> verify ->
                   store bytes under the exact asset key -> immutable asset
                   versions -> node succeeded -> DAG advance

Retries are by error class with bounded backoff and never re-dispatch a paid
call: a failure after the dispatch marker is recorded (failed / uncertain),
not retried. Worker death: see cs_recover_expired_leases.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import struct
import tempfile
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlparse

from ..config import Settings
from ..db import worker_transaction
from ..errors import ServiceError
from ..providers import higgsfield
from ..providers.adapter import engine_higgsfield as _hf
from ..providers.canonical import artifact_digest, digest
from ..providers.contracts import validate
from ..providers.credentials import CredentialError, KeyRing, SealedCredential, materialise

log = logging.getLogger("creative_studio.runtime")

STAGES = ("provider_submit", "ingest_original")
POST_STAGES = ("compose_image", "publish_export", "deliver_handoff")      # CS-17b: free, after the provider returns
MAX_OBJECT_BYTES = 45_000_000            # database-contract per-stage media bound (cs_asset_versions check)
# Backoff (seconds) by retryable error class, indexed by the item's attempt_count.
# Engineering timing, not money; attempts are bounded by cs_work_items.max_attempts (<=3).
BACKOFF_SECONDS = {
    "reference_unavailable": (15, 60, 240),
    "credential_unavailable": (60, 300, 900),
    "output_unavailable": (15, 60, 240),
    "store_unavailable": (15, 60, 240),
    "upstream_unavailable": (15, 60, 240),           # CS-17b post-run stages (MOS gateway)
    "export_readback_mismatch": (15, 60, 240),
}


class Retryable(Exception):
    def __init__(self, error_class: str, detail: str = ""):
        super().__init__(f"{error_class}: {detail}" if detail else error_class)
        self.error_class = error_class


class ObjectStore(Protocol):
    def get(self, storage_key: str) -> bytes: ...
    def put(self, storage_key: str, data: bytes, mime_type: str) -> None: ...


class OutputFetcher(Protocol):
    def fetch(self, url: str, allowed_origins: list[str]) -> bytes: ...


class HttpOutputFetcher:
    """GET a provider output; only https origins the frozen catalogue allows; bounded size."""

    def __init__(self, opener=None):
        self._open = opener or urllib.request.urlopen

    def fetch(self, url: str, allowed_origins: list[str]) -> bytes:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if parsed.scheme != "https" or origin not in allowed_origins:
            raise Retryable("output_unavailable", "result origin is not allowed by the catalogue")
        try:
            with self._open(urllib.request.Request(url, method="GET"), timeout=60) as resp:
                data = resp.read(MAX_OBJECT_BYTES + 1)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise Retryable("output_unavailable", type(exc).__name__) from None
        if len(data) > MAX_OBJECT_BYTES:
            raise Retryable("output_unavailable", "object exceeds the stage media bound")
        return data


def sniff_image(data: bytes) -> tuple[str, int | None, int | None]:
    """(mime, width, height) from magic bytes; never trusts a provider's claimed type."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24 and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return "image/png", (w or None), (h or None)
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", None, None
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", None, None
    raise ValueError("unsupported_output_media")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


BYOK_MESSAGE = "add your Higgsfield key in Integrations"


@dataclass
class ProviderRuntime:
    settings: Settings
    store: ObjectStore
    fetcher: OutputFetcher
    keyring: KeyRing
    recover_batch: int = 2
    last: dict = field(default_factory=dict)
    postrun: object | None = None            # service.postrun.runtime.PostRunRuntime (CS-17b); None = stages not claimed

    # -- entry -------------------------------------------------------------------
    def run_once(self) -> dict:
        if not self.settings.worker_database_url:
            raise ServiceError(503, "worker_database_unconfigured")
        recovered = self.recover()
        with self._tx() as cur:
            cur.execute("select * from public.cs_claim_work(%s, %s, %s::public.cs_work_stage[], 1)",
                        (self.settings.environment_id, self.settings.runtime_principal,
                         list(STAGES) + (list(POST_STAGES) if self.postrun is not None else [])))
            item = cur.fetchone()
        if item is None:
            return {"claimed": 0, "status": "idle", "reason": "no_due_work", "recovered": recovered}
        item = dict(item)
        try:
            if item["stage"] == "provider_submit":
                out = self.provider_submit(item)
            elif item["stage"] in POST_STAGES:
                out = self.postrun.handle(item)
            else:
                out = self.ingest_original(item)
        except Retryable as err:
            out = self._reschedule(item, err)

        out.update(claimed=1, work_item_id=str(item["work_item_id"]), stage=item["stage"], recovered=recovered)
        self.last = out
        return out

    def recover(self) -> list[dict]:
        with self._tx() as cur:
            cur.execute("select * from public.cs_recover_expired_leases(%s, %s, %s)",
                        (self.settings.environment_id, self.recover_batch, self.settings.release_id))
            return [{k: str(v) for k, v in r.items()} for r in cur.fetchall()]

    # -- helpers -----------------------------------------------------------------
    def _tx(self):
        return worker_transaction(self.settings.worker_database_url)

    @staticmethod
    def _lease(item) -> tuple:
        return (item["work_item_id"], item["lease_token"], item["fencing_token"])

    def _reschedule(self, item, err: Retryable) -> dict:
        delays = BACKOFF_SECONDS.get(err.error_class, (60, 300, 900))
        delay = delays[min(max(item["attempt_count"] - 1, 0), len(delays) - 1)]
        with self._tx() as cur:
            cur.execute("select public.cs_reschedule_work(%s, %s, %s, %s, %s)::text as state",
                        (*self._lease(item), err.error_class, delay))
            state = cur.fetchone()["state"]
        log.warning("stage %s rescheduled: %s (%s) -> %s in %ss", item["stage"], err.error_class, err, state, delay)
        return {"status": state, "failure_code": err.error_class, "retry_in_seconds": delay}

    def _abandon(self, item, attempt_id, outcome: str, error: dict) -> dict:
        with self._tx() as cur:
            cur.execute("select public.cs_abandon_attempt(%s, %s, %s, %s, %s, %s::jsonb, %s)::text as run_state",
                        (*self._lease(item), attempt_id, outcome, json.dumps(error), self.settings.release_id))
            return {"status": f"attempt_{outcome}", "run_state": cur.fetchone()["run_state"], "error": error["code"]}

    # -- provider_submit -----------------------------------------------------------
    def provider_submit(self, item) -> dict:
        lease = self._lease(item)
        try:
            with self._tx() as cur:
                cur.execute("select * from public.cs_prepare_attempt(%s, %s, %s, %s)",
                            (*lease, higgsfield.ADAPTER_REVISION))
                att = dict(cur.fetchone())
        except ServiceError as err:
            if err.code == "byok_required":      # CS-19: funding flipped to byok_only after admission
                return self._abandon(item, None, "refused", higgsfield.error_doc("byok_required", BYOK_MESSAGE))
            raise
        if att["attempt_state"] != "prepared":
            # A paid attempt already passed its dispatch marker: never call the provider again.
            if att["attempt_state"] == "dispatching":
                with self._tx() as cur:
                    cur.execute("select public.cs_record_dispatch_uncertain(%s, %s, %s, %s, %s::jsonb, %s)::text as s",
                                (*lease, att["attempt_id"], json.dumps(higgsfield.error_doc(
                                    "duplicate_dispatch_refused", "attempt was already dispatched")),
                                 self.settings.release_id))
                    return {"status": "duplicate_dispatch_refused", "run_state": cur.fetchone()["s"]}
            with self._tx() as cur:
                cur.execute("select public.cs_close_duplicate_submit(%s, %s, %s, %s) as s",
                            (*lease, self.settings.release_id))
                return {"status": "duplicate_dispatch_refused", "attempt_state": cur.fetchone()["s"]}

        with tempfile.TemporaryDirectory(prefix="cs-b3-job-") as job_dir:
            refs = self._materialise_references(item, att, job_dir)              # Retryable
            try:
                sealed = self._credential(item, att)
            except ServiceError as err:
                if err.code == "byok_required":
                    return self._abandon(item, att["attempt_id"], "refused",
                                         higgsfield.error_doc("byok_required", BYOK_MESSAGE))
                raise
            if sealed is None:
                return self._abandon(item, att["attempt_id"], "refused", higgsfield.error_doc(
                    "provider_credentials_missing", "the admitted credential is no longer active"))
            envelope = self._envelope(item, att, refs)
            sealed_envelope = self.keyring.seal_envelope(envelope)          # encrypted at rest
            header = {k: envelope[k] for k in ("schema_version", "workspace_id", "attempt_id", "idempotency_key")}
            try:
                with self._tx() as cur:
                    cur.execute("select public.cs_mark_dispatching(%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)",
                                (*lease, att["attempt_id"], json.dumps(header), sealed_envelope.ciphertext,
                                 sealed_envelope.nonce, sealed_envelope.key_version, envelope["sha256"],
                                 envelope["dispatch_sha256"], digest(envelope["body"])))
            except ServiceError as err:
                if err.code == "run_cancel_requested":
                    return self._abandon(item, att["attempt_id"], "cancelled",
                                         higgsfield.error_doc("run_cancelled", "cancel requested before dispatch"))
                if err.code == "authorization_revoked":
                    return self._abandon(item, att["attempt_id"], "authorization_blocked",
                                         higgsfield.error_doc("authorization_revoked",
                                                              "requester can no longer produce in this workspace"))
                if err.code == "credential_revoked":
                    return self._abandon(item, att["attempt_id"], "refused",
                                         higgsfield.error_doc("credential_revoked", "credential revoked before dispatch"))
                if err.code == "byok_required":
                    return self._abandon(item, att["attempt_id"], "refused",
                                         higgsfield.error_doc("byok_required", BYOK_MESSAGE))
                if err.code == "attempt_already_dispatched":
                    return {"status": "duplicate_dispatch_refused"}
                raise
            # ---- dispatch marker is durable: exactly one provider call from here ----
            try:
                plaintext = self.keyring.open(sealed, provider=higgsfield.PROVIDER_ID,
                                              fingerprint=att["provider_account_fingerprint"])
            except CredentialError as exc:
                return self._record_refused(item, att, higgsfield.error_doc(exc.code, str(exc)))
            with materialise(plaintext) as cred_path:
                credential = _hf.WorkspaceCredential(workspace_id=str(item["workspace_id"]),
                                                 credential_id=str(sealed_id(att)), credentials_path=cred_path,
                                                 provider_workspace_id=None)
                try:
                    results = higgsfield.execute(att["catalogue_surface"], att["provider_call"], att["adapter_config"],
                                                 refs, credential, job_dir)
                    outcome = higgsfield.normalise(results)
                except Exception as exc:  # classified, recorded; never swallowed
                    kind, error = higgsfield.classify(exc)
                    log.warning("provider call ended: %s %s", kind, error["code"])
                    if kind == "refused":
                        return self._record_refused(item, att, error)
                    if kind == "failed":
                        outcome = higgsfield.ProviderOutcome(outcome="failed", error=error)
                    else:
                        with self._tx() as cur:
                            cur.execute("select public.cs_record_dispatch_uncertain(%s, %s, %s, %s, %s::jsonb, %s)::text as s",
                                        (*lease, att["attempt_id"], json.dumps(error), self.settings.release_id))
                            return {"status": "needs_reconciliation", "run_state": cur.fetchone()["s"],
                                    "error": error["code"]}
        with self._tx() as cur:
            cur.execute("""select public.cs_record_provider_outcome(%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s)::text
                             as s""", (*lease, att["attempt_id"], outcome.outcome, json.dumps(outcome.jobs),
                                       json.dumps(outcome.charges), json.dumps(outcome.error), self.settings.release_id))
            run_state = cur.fetchone()["s"]
        return {"status": "provider_" + outcome.outcome, "run_state": run_state, "jobs": len(outcome.jobs),
                "charges": [{k: c[k] for k in ("billing_reference", "certainty", "credits")} for c in outcome.charges]}

    def _record_refused(self, item, att, error) -> dict:
        return self._abandon(item, att["attempt_id"], "refused", error)

    def _materialise_references(self, item, att, job_dir) -> list[dict]:
        call = att["provider_call"]
        by_port: dict[str, list] = {}
        for i in att["inputs"]:
            by_port.setdefault(i["port"], []).append(i["asset"])
        refs = []
        for b in sorted(call.get("media_bindings") or [], key=lambda b: (b["field"], b["ordinal"])):
            assets = by_port.get(b["input_port"]) or []
            if b["ordinal"] >= len(assets):
                raise Retryable("reference_unavailable", f"port {b['input_port']} has no input {b['ordinal']}")
            asset = assets[b["ordinal"]]
            with self._tx() as cur:
                cur.execute("""select storage_key, sha256, mime_type from public.cs_asset_versions
                                where workspace_id = %s and id = %s and asset_id = %s""",
                            (item["workspace_id"], asset["asset_version_id"], asset["asset_id"]))
                row = cur.fetchone()
            if row is None or row["sha256"] != asset["sha256"]:
                raise Retryable("reference_unavailable", "reference version missing or digest mismatch")
            try:
                data = self.store.get(row["storage_key"])
            except Exception as exc:
                raise Retryable("reference_unavailable", type(exc).__name__) from None
            if hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise Retryable("reference_unavailable", "stored bytes do not match the recorded digest")
            ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(row["mime_type"], ".bin")
            path = os.path.join(job_dir, f"ref-{len(refs)}{ext}")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            refs.append({"path": path, "role": b["role"], "field": b["field"], "ordinal": b["ordinal"], "asset": asset})
        return refs

    def _credential(self, item, att) -> SealedCredential | None:
        with self._tx() as cur:
            cur.execute("select * from public.cs_attempt_credential(%s, %s, %s, %s)",
                        (*self._lease(item), att["attempt_id"]))
            row = cur.fetchone()
        if row is None:
            return None
        att["_credential_id"] = row["credential_id"]
        return SealedCredential(bytes(row["ciphertext"]), bytes(row["nonce"]), row["encryption_key_version"])

    def _envelope(self, item, att, refs) -> dict:
        entry = att["catalogue_entry"]
        call = att["provider_call"]
        body = {"model_id": call["model_id"], "endpoint_key": call["endpoint_key"],
                "positive_prompt": call["positive_prompt"], "negative_prompt": call.get("negative_prompt"),
                "parameters": call.get("parameters") or {}, "seed": call.get("seed"),
                "media": [{"field": r["field"], "role": r["role"], "ordinal": r["ordinal"],
                           "sha256": r["asset"]["sha256"]} for r in refs]}
        endpoint = entry["submit"]["origin"] + entry["submit"]["path_template"]
        account = str(att["credential_version_id"])
        envelope = {
            "schema_version": "cs-dispatch-envelope-v1", "id": str(uuid.uuid4()),
            "workspace_id": str(item["workspace_id"]), "attempt_id": str(att["attempt_id"]), "created_at": _now(),
            "sha256": "", "provider_account_binding_id": account, "catalogue_entry_id": entry["id"],
            "endpoint": endpoint, "method": "POST", "idempotency_key": str(att["provider_idempotency_key"]),
            "body": body, "webhook_url": None, "expires_after_header": None,
            "media_bindings": [{"asset": r["asset"], "field": r["field"], "ordinal": r["ordinal"], "url": None,
                                "provider_media_id": None, "expires_at": None} for r in refs],
            "credential_binding_id": account,
            "dispatch_sha256": digest({"account": account, "endpoint": endpoint, "body": body, "webhook": None,
                                       "expiry_header": None}),
        }
        envelope["sha256"] = artifact_digest(envelope)
        validate("dispatch-envelope-v1.schema.json", envelope)
        return envelope

    # -- ingest_original -------------------------------------------------------------
    def ingest_original(self, item) -> dict:
        lease = self._lease(item)
        with self._tx() as cur:
            cur.execute("select * from public.cs_ingest_targets(%s, %s, %s)", lease)
            targets = [dict(r) for r in cur.fetchall()]
        fetched = []
        for t in targets:
            data = self.fetcher.fetch(t["result_url"], list(t["result_origins"]))     # Retryable
            try:
                mime, width, height = sniff_image(data)
            except ValueError:
                raise Retryable("output_unavailable", "provider output is not a supported image") from None
            fetched.append((t, data, mime, width, height))
        with self._tx() as cur:
            refs = []
            for t, data, mime, width, height in fetched:
                cur.execute("select * from public.cs_commit_asset_version(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (*lease, None, f"{item['node_id']} output {t['ordinal'] + 1}", "generated", mime,
                             len(data), hashlib.sha256(data).hexdigest(), width, height, None, "[]",
                             self.settings.runtime_principal))
                row = cur.fetchone()
                try:
                    self.store.put(row["storage_key"], data, mime)
                except Exception as exc:
                    raise Retryable("store_unavailable", type(exc).__name__) from None
                refs.append({"authority": "creative-studio", "workspace_id": str(item["workspace_id"]),
                             "asset_id": str(row["asset_id"]), "asset_version_id": str(row["asset_version_id"]),
                             "revision": row["revision"], "sha256": hashlib.sha256(data).hexdigest(),
                             "mime_type": mime})
            cur.execute("select public.cs_complete_ingest(%s, %s, %s, %s::jsonb, %s)::text as s",
                        (*lease, json.dumps(refs), self.settings.release_id))
            run_state = cur.fetchone()["s"]
        return {"status": "ingested", "run_state": run_state, "outputs": refs}


def sealed_id(att) -> str:
    return str(att.get("_credential_id") or att["credential_version_id"])
