"""``import_brand_package`` worker stage (operation-owned, lease-fenced).

1. Read MOS's export of the exact admitted BrandKit version; record MOS's current
   head (so staleness is evidence, not a guess).
2. Build the brand-package-v1 document (typed refusal -> operation failed).
3. For every referenced file: fetch the exact version from MOS, verify byte
   SHA-256 and size, write it to the private bucket at its exact key, then record
   the immutable mirror (replay reuses an existing mirror without refetching).
4. Attach the package; SQL re-checks approval, staleness, file coverage and
   digests before the operation can complete.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from urllib.parse import quote
import urllib.error
import urllib.request

from service.config import Settings
from service.db import worker_transaction
from service.errors import ServiceError
from service.storage import BUCKET
from service.requests.canonical import canonical_text
from service.worker import Handler, RetryableStageError, StageResult, WorkContext, run_once

from .errors import InputRefusal
from .gateway import GatewayUnavailable, MarketingOsGateway, ObjectWriter
from .mos_brandkit import build_package

MAX_FILE_BYTES = 45_000_000   # same per-object bound as cs_asset_versions / upload intents (CS-B1)
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/svg+xml": "svg", "video/mp4": "mp4",
        "audio/mp4": "m4a", "font/ttf": "ttf", "font/otf": "otf", "font/woff2": "woff2", "application/pdf": "pdf"}
STAGE = "import_brand_package"


class StorageObjectWriter:
    """Writes as the runtime principal through Storage (its RLS admits only that principal)."""

    def __init__(self, settings: Settings, token_provider, opener=None):
        self.settings = settings
        self.token_provider = token_provider
        self._open = opener or urllib.request.urlopen

    def put(self, storage_key: str, data: bytes, mime_type: str) -> None:
        req = urllib.request.Request(
            f"{self.settings.storage_url}/object/{BUCKET}/{quote(storage_key, safe='/')}", method="POST", data=data,
            headers={"Authorization": f"Bearer {self.token_provider()}", "apikey": self.settings.storage_anon_key,
                     "Content-Type": mime_type, "x-upsert": "false"})
        try:
            with self._open(req, timeout=30) as resp:
                if resp.status not in (200, 201):
                    raise RetryableStageError("storage_write_failed", 30)
        except urllib.error.HTTPError as exc:
            raise RetryableStageError("storage_write_failed", 30) from exc
        except urllib.error.URLError as exc:
            raise RetryableStageError("storage_unavailable", 30) from exc


def _lease(item: dict) -> tuple:
    return (str(item["work_item_id"]), str(item["lease_token"]), item["fencing_token"])


def _fail(settings: Settings, item: dict, refusal: InputRefusal) -> StageResult:
    with worker_transaction(settings.worker_database_url) as cur:
        cur.execute("select public.cs_import_fail(%s, %s, %s, %s, %s, %s)",
                    (*_lease(item), refusal.code, refusal.message[:2000], json.dumps(refusal.field_paths)))
    return StageResult(result_kind="operation_failed", receipt={"code": refusal.code, "message": refusal.message[:300]})


def make_import_handler(settings: Settings, gateway: MarketingOsGateway, writer: ObjectWriter) -> Handler:
    def handler(item: dict, ctx: WorkContext) -> StageResult:
        lease = _lease(item)
        with worker_transaction(settings.worker_database_url) as cur:
            cur.execute("""select w.payload_ref, w.operation_id, b.external_workspace_id, b.authority::text as authority
                             from public.cs_work_items w
                             join public.cs_external_bindings b
                               on b.workspace_id = w.workspace_id and b.id = (w.payload_ref ->> 'binding_id')::uuid
                            where w.id = %s""", (lease[0],))
            row = cur.fetchone()
        package_ref = row["payload_ref"]["package_ref"]
        mos_ws = row["external_workspace_id"]
        try:
            export = gateway.export_brand_kit(mos_workspace_id=mos_ws, artifact_id=package_ref["id"])
        except GatewayUnavailable:
            raise RetryableStageError("upstream_unavailable", 30) from None
        current = export.get("current") or {}
        if current.get("version_id") and current.get("revision"):
            with worker_transaction(settings.worker_database_url) as cur:
                cur.execute("select public.cs_import_observe_source(%s,%s,%s,%s,%s,%s,%s,%s)",
                            (*lease, "BrandKit", current["version_id"], current["revision"], current.get("status"),
                             settings.runtime_principal))
        try:
            build = build_package(export)
            if build.source_ref["sha256"] != package_ref["sha256"] or build.source_ref["version_id"] != package_ref["version_id"]:
                raise InputRefusal("source_revision_changed", "exported BrandKit differs from the admitted reference")
            mirrored = []
            for f in build.files:
                mirrored.append(_mirror(settings, gateway, writer, item, lease, mos_ws, f))
            with worker_transaction(settings.worker_database_url) as cur:
                cur.execute("select * from public.cs_import_attach_package(%s,%s,%s,%s,%s,%s,%s,%s)",
                            (*lease, json.dumps(build.document), canonical_text(
                                {k: v for k, v in build.document.items() if k != "sha256"}),
                             json.dumps(build.source_ref), build.source_status, json.dumps(build.approval_receipt_ref)))
                attached = dict(cur.fetchone())
        except InputRefusal as refusal:
            return _fail(settings, item, refusal)
        except ServiceError as err:
            if err.status in (403, 409, 422):      # typed SQL refusal: not retryable
                return _fail(settings, item, InputRefusal(err.code, err.code))
            raise
        return StageResult(result_kind="brand_package_imported", input_sha256=package_ref["sha256"],
                           output_refs=mirrored,
                           receipt={"package_version_id": str(attached["version_id"]), "sha256": attached["sha256"],
                                    "reused": attached["reused"], "files": len(mirrored)})
    return handler


def _mirror(settings, gateway, writer, item, lease, mos_ws, f: dict) -> dict:
    ref = f["ref"]
    ws = str(item["workspace_id"])
    with worker_transaction(settings.worker_database_url) as cur:
        cur.execute("""select m.asset_id, m.asset_version_id, v.revision, v.sha256, v.mime_type
                         from public.cs_source_asset_mirrors m
                         join public.cs_asset_versions v on v.workspace_id = m.workspace_id and v.id = m.asset_version_id
                        where m.workspace_id = %s and m.source_workspace_id = %s
                          and m.source_asset_version_id = %s and m.source_sha256 = %s""",
                    (ws, mos_ws, ref["asset_version_id"], ref["sha256"]))
        existing = cur.fetchone()
    if existing:
        return _studio_ref(ws, existing)
    ext = _EXT.get(ref["mime_type"])
    if ext is None:
        raise InputRefusal("unsupported_media_type", f"{f['name']}: {ref['mime_type']}")
    try:
        data = gateway.fetch_asset(mos_workspace_id=mos_ws, ref=ref, purpose="embed", max_bytes=MAX_FILE_BYTES)
    except GatewayUnavailable:
        raise RetryableStageError("upstream_unavailable", 30) from None
    if len(data) > MAX_FILE_BYTES:
        raise InputRefusal("asset_hash_mismatch", f"{f['name']}: exceeds size bound")
    digest = hashlib.sha256(data).hexdigest()
    if digest != ref["sha256"] or len(data) != f["size_bytes"]:
        raise InputRefusal("asset_hash_mismatch", f"{f['name']}: received bytes differ from the declared version")
    asset_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
    key = f"{settings.environment_id}/{ws}/{asset_id}/{version_id}/{digest}.{ext}"
    writer.put(key, data, ref["mime_type"])      # bytes durable before the version row exists
    with worker_transaction(settings.worker_database_url) as cur:
        cur.execute("select * from public.cs_import_commit_asset(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (*lease, json.dumps(ref), asset_id, version_id, f["name"], ref["mime_type"], len(data), digest,
                     f.get("width_px"), f.get("height_px"), key, settings.runtime_principal))
        row = dict(cur.fetchone())
    return {"authority": "creative-studio", "workspace_id": ws, "asset_id": str(row["asset_id"]),
            "asset_version_id": str(row["asset_version_id"]), "revision": 1, "sha256": digest,
            "mime_type": ref["mime_type"]}


def _studio_ref(ws: str, row: dict) -> dict:
    return {"authority": "creative-studio", "workspace_id": ws, "asset_id": str(row["asset_id"]),
            "asset_version_id": str(row["asset_version_id"]), "revision": row["revision"], "sha256": row["sha256"],
            "mime_type": row["mime_type"]}


def run_import_once(settings: Settings, gateway: MarketingOsGateway, writer: ObjectWriter) -> dict:
    """One worker invocation restricted to the brand import stage."""
    return run_once(settings, {STAGE: make_import_handler(settings, gateway, writer)})
