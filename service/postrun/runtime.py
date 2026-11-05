"""CS-17b post-run stages on the durable work queue: compose_image -> publish_export -> deliver_handoff.

All three are FREE (no provider call, no reservation): the Produce click was the only spend. Every stage reads one
lease-checked context (`cs_post_context`), does its work with typed refusals, and finishes with a typed state
(`cs_post_finish`) that run status shows. Nothing is marked delivered without MOS's verified receipt.

  compose_image   layout (service.postrun.layout) from the generated image + the EXACT brand logo/font files of the
                  pinned brand package + the request's exact copy -> saved composition version -> trial render
                  (same inputs => identical bytes) -> state `composed` | `compose_blocked: <code>`
  publish_export  re-render, require the same bytes hash, persist the file, build + publish the cs-export-v2 manifest
                  -> `exported` | `export_blocked: <code>`
  deliver_handoff cs-mos-handoff-v1 built from the manifest + request origin, queued (immutable), delivered through the
                  signed gateway; `handoff_delivered` only with a verified MOS ingestion receipt, `handoff_offered`
                  while MOS has not yet pulled/acknowledged it, `handoff_failed` after bounded retries.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from PIL import Image

from ..catalogue.creative import CatalogueError, get_shot, load_catalogue
from ..composition import fontinfo
from ..composition.assets import same_ref
from ..composition.canonical import artifact_sha256
from ..composition.errors import CompositionError
from ..composition.export import IMAGE_PRESETS, build_export, version_ref
from ..composition.handoff import build_handoff, verify_ingestion_receipt
from ..composition.renderer import render_artboard
from ..composition.routes import _input_refs
from ..composition.validate import semantic_sha256, validate_composition
from ..db import worker_transaction
from ..errors import ServiceError
from ..runtime.worker import Retryable           # same class: the runtime reschedules it with its backoff table
from ..brand.gateway import GatewayUnavailable
from . import layout

log = logging.getLogger("creative_studio.postrun")

STAGES = ("compose_image", "publish_export", "deliver_handoff")
# Retry backoff (seconds) by error class, indexed by attempt (engineering timing, bounded by max_attempts <= 3).
BACKOFF_SECONDS = {"upstream_unavailable": (15, 60, 240), "store_unavailable": (15, 60, 240),
                   "export_readback_mismatch": (15, 60, 240)}
_SUCCESS_STATE = {"compose_image": "composed", "publish_export": "exported", "deliver_handoff": "handoff_delivered"}
_FAILURE_STATE = {"compose_image": "compose_blocked", "publish_export": "export_blocked",
                  "deliver_handoff": "handoff_failed"}
_EXT_MIME = {"image/png", "image/jpeg", "image/webp"}


class Blocked(Exception):
    """A typed, non-retryable refusal: the run stays completed; the reason is visible in run status."""

    def __init__(self, code: str, message: str = "", related: list | None = None):
        super().__init__(message or code)
        self.code, self.message, self.related = code, message or code, related or []


def _refinish(ctx) -> int:
    """How many times the owner re-finished this canvas (0 = the first finish keeps its original identities)."""
    return int((ctx.get("state") or {}).get("refinish") or 0)


def _text_zone(request) -> str:
    """The shot's text_zone (catalogue) for a request carrying a shot_spec; legacy requests keep the bottom stack."""
    spec = request.get("shot_spec")
    if not spec:
        return "bottom"
    try:
        return get_shot(load_catalogue(), spec["shot_id"])["text_zone"]
    except CatalogueError as exc:   # a catalogue update dropped the shot: block this canvas, never crash compose
        raise Blocked("invalid_contract", f"shot {spec.get('shot_id')!r}: {exc}") from None


def _iso(value) -> str:
    """Whole-second UTC timestamp (identical across retries of the same stage)."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StoreAssetSource:
    """Exact-byte source for the renderer: private storage, addressed by the asset version recorded in the context."""

    def __init__(self, store, keys: dict[str, str]):
        self.store, self.keys = store, keys

    def fetch(self, ref: dict):
        key = self.keys.get(ref["asset_version_id"])
        if key is None:
            return None
        try:
            return self.store.get(key)
        except Exception as exc:                              # storage trouble is retryable, never a silent miss
            raise Retryable("store_unavailable", type(exc).__name__) from None


class PostRunRuntime:
    def __init__(self, settings, store, gateway=None, assertion_key: str | None = None, clock=time.time):
        self.settings, self.store, self.gateway = settings, store, gateway
        self.assertion_key = assertion_key if assertion_key is not None else getattr(settings, "mos_assertion_key", None)
        self.clock = clock

    # -- entry (called by ProviderRuntime.run_once for post-run stages) ------------------------------------
    def handle(self, item: dict) -> dict:
        lease = (item["work_item_id"], item["lease_token"], item["fencing_token"])
        ctx = self._context(lease)
        stage = item["stage"]
        try:
            if stage == "compose_image":
                return self.compose(lease, ctx)
            if stage == "publish_export":
                return self.export(lease, ctx)
            return self.deliver(lease, ctx)
        except Blocked as blocked:
            return self._finish(lease, _FAILURE_STATE[stage], blocked.code,
                                {"message": blocked.message[:500], "related_ids": blocked.related[:20]}, {}, None)
        except Retryable as err:
            if ctx["work"]["attempt_count"] >= ctx["work"]["max_attempts"]:
                state = _FAILURE_STATE[stage]
                self._record_failure(lease, state, err.error_class, {"message": str(err)[:500],
                                                                     "attempts": ctx["work"]["attempt_count"]})
                return {"status": state, "reason": err.error_class}
            raise

    def backoff(self, error_class: str, attempt_count: int) -> int:
        delays = BACKOFF_SECONDS.get(error_class, (60, 300, 900))
        return delays[min(max(attempt_count - 1, 0), len(delays) - 1)]

    # -- db helpers -------------------------------------------------------------------------------------------
    def _tx(self):
        return worker_transaction(self.settings.worker_database_url)

    def _context(self, lease) -> dict:
        with self._tx() as cur:
            cur.execute("select public.cs_post_context(%s,%s,%s) as c", lease)
            return cur.fetchone()["c"]

    def _finish(self, lease, state, reason, detail, patch, next_stage) -> dict:
        with self._tx() as cur:
            cur.execute("select public.cs_post_finish(%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s) as s",
                        (*lease, state, reason, json.dumps(detail), json.dumps(patch), next_stage,
                         self.settings.release_id))
            out = cur.fetchone()["s"]
        return {"status": out, "reason": reason, "next_stage": next_stage}

    def _record_failure(self, lease, state, reason, detail) -> None:
        with self._tx() as cur:
            cur.execute("select public.cs_post_record_failure(%s,%s,%s,%s,%s,%s::jsonb,%s)",
                        (*lease, state, reason, json.dumps(detail), self.settings.release_id))

    def _assets(self, ctx) -> tuple[dict, dict]:
        """(storage keys by asset_version_id, records by asset_version_id) for everything composed/exported."""
        keys, infos = {}, {}
        for m in ctx["mirrors"]:
            keys[m["ref"]["asset_version_id"]] = m["storage_key"]
            infos[m["ref"]["asset_version_id"]] = m
        for o in ctx["outputs"]:
            keys[o["ref"]["asset_version_id"]] = o["storage_key"]
            infos[o["ref"]["asset_version_id"]] = o
        return keys, infos

    # -- shared geometry ----------------------------------------------------------------------------------------
    @staticmethod
    def _canvas(ctx) -> tuple[dict, dict]:
        node = ctx["work"]["canvas_node_id"]
        out = next((o for o in ctx["plan"]["program"]["outputs"] if o["node_id"] == node), None)
        if out is None:
            raise Blocked("canvas_not_in_plan", f"plan has no output for {node}")
        fmt = out["format"]
        if fmt.get("kind") != "image":
            raise Blocked("unsupported_media_kind", f"{fmt.get('kind')} composition is not part of this pipeline")
        if fmt.get("preset") not in IMAGE_PRESETS:
            raise Blocked("unsupported_export_preset", f"{fmt.get('preset')!r} is not an image export preset")
        return out, fmt

    # -- compose_image -------------------------------------------------------------------------------------------
    def compose(self, lease, ctx) -> dict:
        _, fmt = self._canvas(ctx)
        package, request = ctx["package"], ctx["request"]
        if package is None or request is None:
            raise Blocked("source_unavailable", "brand package or creative request is not readable")
        if not ctx["outputs"]:
            raise Blocked("generated_asset_missing", "the provider node has no persisted output")
        keys, infos = self._assets(ctx)
        source = StoreAssetSource(self.store, keys)
        scene = ctx["outputs"][0]["ref"]
        logo_spec = next((l for l in package["logos"] if l["role"] == "primary"), package["logos"][0] if package["logos"] else None)
        if logo_spec is None:
            raise Blocked("logo_asset_missing", "brand package has no logo")
        logo_m = self._mirror_for(ctx, logo_spec["asset"])
        if logo_m is None:
            raise Blocked("logo_asset_missing", "the package logo file has no Studio-owned copy", [logo_spec["id"]])
        logo_bytes = source.fetch(logo_m["ref"])
        if logo_bytes is None or hashlib.sha256(logo_bytes).hexdigest() != logo_m["ref"]["sha256"]:
            raise Blocked("logo_asset_missing", "the package logo bytes are unavailable or differ from the pinned hash",
                          [logo_spec["id"]])
        try:
            with Image.open(io.BytesIO(logo_bytes)) as im:
                logo_px = im.size
        except Exception:
            raise Blocked("logo_asset_missing", "the package logo is not a decodable raster image", [logo_spec["id"]])
        chosen, not_rendered = layout.pick_copy(request["exact_copy"])
        text_zone = _text_zone(request)
        if text_zone == "none":          # the shot reserved no text space: lay out no text and record all copy as unrendered
            chosen = {"headline": None, "body": None, "cta": None, "disclaimers": []}
            not_rendered = sorted(c["id"] for c in request["exact_copy"])
        library = request["origin"].get("kind") == "marketing-os-library"   # image + logo layer, no campaign copy
        has_text = any(chosen[k] for k in ("headline", "body", "cta")) or bool(chosen["disclaimers"])
        if not library and not has_text and text_zone != "none":
            raise Blocked("exact_copy_missing", "the request has no headline, body, cta or disclaimer copy to compose")
        # Fonts are needed only to set text. A text-free composition (image library: scene + logo) never loads them, so
        # a brand whose fonts are web formats (WOFF2) can still get images; text compositions still require sfnt files.
        text_skipped = None
        try:
            fonts, weights = self._fonts(ctx, source) if has_text else ({}, {})
        except Blocked as err:
            if not err.code.startswith("font_"):
                raise
            # The brand's fonts can't set text (e.g. a CSS font stack, not a font file): deliver the picture + logo
            # and leave the wording to the post itself, instead of withholding the picture (2026-11-05, Regenic).
            text_skipped = {"code": err.code, "message": err.message}
            chosen = {"headline": None, "body": None, "cta": None, "disclaimers": []}
            not_rendered = sorted(c["id"] for c in request["exact_copy"])
            text_zone, fonts, weights = "none", {}, {}
        board_id = fmt["id"]
        zone = layout.safe_zone_for(fmt["preset"], fmt["width_px"], fmt["height_px"])
        safe_rect = zone.rect(fmt["width_px"], fmt["height_px"])
        last = None
        for step in layout.LADDER:
            layers = layout.build_layers(
                width=fmt["width_px"], height=fmt["height_px"], safe_rect=safe_rect, scene=scene, logo=logo_m["ref"],
                logo_id=logo_spec["id"], logo_px=logo_px, clear_space_ratio=logo_spec["clear_space_ratio"],
                fonts=fonts, weights=weights, chosen=chosen, palette=package["palette"], step=step,
                text_zone=text_zone)
            doc = layout.composition_doc(
                workspace_id=ctx["plan"]["workspace_id"], run_id=ctx["run"]["id"], canvas=ctx["work"]["canvas_node_id"],
                created_at=ctx["plan"]["created_at"], created_by=ctx["run"]["requested_by"],
                recipe=ctx["plan"]["sources"]["recipe"], artboard_id=board_id, width=fmt["width_px"],
                height=fmt["height_px"], layers=layers, refinish=_refinish(ctx))
            doc["sha256"] = artifact_sha256(doc)
            try:
                validate_composition(doc)
                render = render_artboard(doc, board_id, brand_package=package, assets=source, safe_zone=zone,
                                         mime_type=fmt["mime_type"])
                break
            except CompositionError as err:
                last = err
                if err.code != "text_overflow":
                    raise Blocked(err.code, err.message, list(getattr(err, "related_ids", []) or []))
        else:
            raise Blocked(last.code, last.message, list(getattr(last, "related_ids", []) or []))
        with self._tx() as cur:
            cur.execute("select * from public.cs_post_save_composition(%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s)",
                        (*lease, json.dumps(doc), semantic_sha256(doc), json.dumps(_input_refs(doc)),
                         self.settings.runtime_principal))
            saved = dict(cur.fetchone())
        detail = {"layout_version": layout.LAYOUT_VERSION, "type_scale_step": step, "copy_not_rendered": not_rendered,
                  "render_sha256": render.sha256, "renderer_version": render.renderer_version,
                  "model_drawn_text": False, "text_skipped": text_skipped}
        return self._finish(lease, "composed", None, detail,
                            {"composition_version_id": str(saved["version_id"]), "render_sha256": render.sha256},
                            "publish_export")

    @staticmethod
    def _mirror_for(ctx, source_ref: dict):
        return next((m for m in ctx["mirrors"] if m["source_sha256"] == source_ref["sha256"]
                     and m["source_asset_version_id"] == source_ref["asset_version_id"]), None)

    def _fonts(self, ctx, source) -> tuple[dict, dict]:
        """Package font files (never substituted). The weight is the font file's own OS/2 weight, which must be listed
        by the package typography."""
        out, weights = {}, {}
        for role in ("heading", "body"):
            candidates = [t for t in ctx["package"]["typography"] if t["role"] == role] or \
                         [t for t in ctx["package"]["typography"] if t["role"] == "heading"] or \
                         list(ctx["package"]["typography"])
            found = None
            for typo in candidates:
                for fa in typo["font_assets"]:
                    m = self._mirror_for(ctx, fa)
                    data = source.fetch(m["ref"]) if m else None
                    if data is None:
                        continue
                    try:
                        info = fontinfo.inspect(data)
                    except CompositionError as err:
                        raise Blocked(err.code, err.message, [typo["id"]]) from None
                    if info.weight_class in typo["weights"]:
                        found = (m["ref"], info.weight_class)
                        break
                if found:
                    break
            if found is None:
                code = "font_asset_missing" if not any(self._mirror_for(ctx, fa) for t in candidates
                                                        for fa in t["font_assets"]) else "font_weight_missing"
                raise Blocked(code, "no usable licensed package font file for role " + role +
                              "; no substitute font is used")
            out[role], weights[role] = found
        return out, weights

    # -- publish_export --------------------------------------------------------------------------------------------
    def export(self, lease, ctx) -> dict:
        if ctx.get("existing_export"):                       # published before a crash: finish, never re-publish
            return self._finish(lease, "exported", None, {"reused_export": True},
                                {"export_version_id": ctx["existing_export"]["version_id"]}, "deliver_handoff")
        _, fmt = self._canvas(ctx)
        comp, state = ctx["composition"], ctx["state"]
        if comp is None or not state.get("render_sha256"):
            raise Blocked("source_unavailable", "no saved composition to export")
        keys, infos = self._assets(ctx)
        source = StoreAssetSource(self.store, keys)
        zone = layout.safe_zone_for(fmt["preset"], fmt["width_px"], fmt["height_px"])
        try:
            render = render_artboard(comp, fmt["id"], brand_package=ctx["package"], assets=source, safe_zone=zone,
                                     mime_type=fmt["mime_type"])
        except CompositionError as err:
            raise Blocked(err.code, err.message, list(getattr(err, "related_ids", []) or []))
        if render.sha256 != state["render_sha256"]:
            raise Blocked("render_not_reproducible", "the re-render differs from the composed render; refusing to "
                          "export bytes that were not checked")
        records, generated = self._source_records(ctx, infos)
        plan = ctx["plan"]
        lineage = {"request": plan["sources"]["request"], "treatment": plan["sources"]["treatment"],
                   "recipe": plan["sources"]["recipe"],
                   "plan": version_ref(plan, authority="creative-studio", kind="execution_plan"),
                   "compile_hash": plan["compile_hash"], "catalogue": plan["sources"]["catalogue"]}
        copy_sources = {c["id"]: c["source_copy"] for c in ctx["request"]["exact_copy"]}
        store = _LeaseExportStore(self, lease)
        try:
            manifest = build_export(render, composition=comp, brand_package=ctx["package"], fmt=fmt, store=store,
                                    source_records=records, generated_sources=generated, lineage=lineage,
                                    copy_sources=copy_sources, cost=ctx["cost"], created_by=ctx["run"]["requested_by"],
                                    created_at=_iso(state["composed_at"]),
                                    copy_required=ctx["request"]["origin"].get("kind") != "marketing-os-library"
                                    and _text_zone(ctx["request"]) != "none")
        except CompositionError as err:
            if err.retryable:
                raise Retryable(err.code, err.message) from None
            raise Blocked(err.code, err.message, list(getattr(err, "related_ids", []) or []))
        with self._tx() as cur:
            cur.execute("select * from public.cs_publish_export(%s,%s,%s,%s::jsonb,%s)",
                        (*lease, json.dumps(manifest), self.settings.runtime_principal))
            published = dict(cur.fetchone())
        return self._finish(lease, "exported", None,
                            {"file_sha256": manifest["file"]["ref"]["sha256"], "preset": fmt["preset"],
                             "cost_state": manifest["cost"]["state"]},
                            {"export_version_id": str(published["export_version_id"])}, "deliver_handoff")

    def _source_records(self, ctx, infos) -> tuple[list[dict], list[dict]]:
        comp = ctx["composition"]
        board = comp["artboards"][0]
        pkg_records = {(r["ref"]["asset_version_id"], r["ref"]["sha256"]): r for r in ctx["package"]["assets"]}
        records, generated = [], []
        provider = (ctx["plan"]["program"]["nodes"][0]["provider_call"]["provider"]
                    if ctx["plan"]["program"]["nodes"] else "provider")
        receipts = [r["id"] for r in ctx["provider_receipts"]]
        for layer in board["layers"]:
            content = layer["content"]
            for key in ("asset", "font"):
                ref = content.get(key) if isinstance(content, dict) else None
                if not ref:
                    continue
                info = infos[ref["asset_version_id"]]
                out = next((o for o in ctx["outputs"] if o["ref"]["asset_version_id"] == ref["asset_version_id"]), None)
                if out is not None:
                    records.append(self._generated_record(ctx, out, provider, receipts))
                    generated.append({"run_id": ctx["run"]["id"], "plan_id": ctx["plan"]["id"],
                                      "compile_hash": ctx["plan"]["compile_hash"], "node_id": ctx["work"]["canvas_node_id"],
                                      "attempt_id": ctx["attempt_id"], "output_port": "image", "asset": ref})
                    continue
                m = infos[ref["asset_version_id"]]
                src = m["source_ref"]
                pr = pkg_records.get((src["asset_version_id"], src["sha256"]))
                if pr is None:
                    raise Blocked("source_lineage_missing", "package asset record with rights not found",
                                  [ref["asset_version_id"]])
                records.append({**pr, "ref": ref, "name": m["name"][:255], "size_bytes": m["size_bytes"],
                                "width_px": m["width"], "height_px": m["height"], "parent_assets": [src]})
        return records, generated

    @staticmethod
    def _generated_record(ctx, out, provider, receipts) -> dict:
        """Rights for a provider output come from the provider terms of the account that paid for it (basis
        `provider_terms`); the receipt ids that evidence the paid generation are recorded as restrictions text.
        The operator must confirm the terms permit commercial use (see report)."""
        at = _iso(ctx["plan"]["created_at"])
        by = ctx["run"]["requested_by"]
        rights = {"receipt_id": layout.u5("rights", ctx["run"]["id"], out["ref"]["asset_version_id"]), "revision": 1,
                  "sha256": "", "status": "permitted", "holder": str(provider)[:200], "basis": "provider_terms",
                  "evidence": [], "allowed_uses": ["reference", "embed", "redistribute"], "channels": ["*"],
                  "territories": ["*"], "valid_from": at, "expires_at": None, "attribution": None,
                  "restrictions": ["asserted from the paying account's provider terms; evidenced by provider receipts: "
                                   + (", ".join(receipts) or "none recorded")][:40], "recorded_by": by}
        rights["sha256"] = artifact_sha256(rights)
        return {"ref": out["ref"], "name": (out.get("name") or "generated output")[:255], "size_bytes": out["size_bytes"],
                "width_px": out["width"], "height_px": out["height"], "duration_ms": None, "rights": rights,
                "origin": "provider", "source_url": None, "captured_at": None, "parent_assets": []}

    # -- deliver_handoff -------------------------------------------------------------------------------------------
    def deliver(self, lease, ctx) -> dict:
        export = ctx.get("existing_export")
        if not export:
            raise Blocked("export_unavailable", "no published export for this canvas")
        request = ctx["request"]
        origin = request["origin"]
        if origin.get("kind") not in ("marketing-os", "marketing-os-library"):
            return self._finish(lease, "handoff_not_applicable", "standalone_request", {}, {}, None)
        if not ctx.get("binding"):
            raise Blocked("mos_connection_required", "no active verified Marketing OS connection for this workspace")
        keys = (("library", "library_item_key", "policy") if origin["kind"] == "marketing-os-library"
                else ("concept", "placement", "copy_versions", "destination", "claim_set", "policy"))
        target = {k: origin[k] for k in keys}
        target = {"authority": "marketing-os", "workspace_id": origin["mos_workspace_id"], **target}
        key = layout.u5("handoff", ctx["run"]["id"], ctx["work"]["canvas_node_id"]) if not _refinish(ctx) else \
            layout.u5("handoff", ctx["run"]["id"], ctx["work"]["canvas_node_id"], "refinish", str(_refinish(ctx)))
        try:
            doc = build_handoff([export], target=target, creative_request=export["lineage"]["request"],
                                idempotency_key=key, created_by=ctx["run"]["requested_by"],
                                created_at=_iso(ctx["state"]["exported_at"]))
        except CompositionError as err:
            raise Blocked(err.code, err.message, list(getattr(err, "related_ids", []) or []))
        request_sha = hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()
        with self._tx() as cur:
            try:
                cur.execute("select * from public.cs_post_enqueue_handoff(%s,%s,%s,%s::jsonb,%s,%s)",
                            (*lease, json.dumps(doc), request_sha, self.settings.runtime_principal))
            except ServiceError as err:
                raise Blocked(err.code, str(err.detail or err.code)) from None
            row = dict(cur.fetchone())
        patch = {"handoff_id": doc["id"]}
        if row["state"] == "received":
            return self._finish(lease, "handoff_delivered", None, {"replayed": True}, patch, None)
        if self.gateway is None or not self.assertion_key:
            return self._finish(lease, "handoff_offered", "mos_delivery_unconfigured",
                                {"detail": "handoff is queued and readable by Marketing OS; no push credentials"},
                                patch, None)
        self._set_handoff(lease, doc["id"], "delivering", None)
        try:
            reply = self.gateway.deliver_handoff(mos_workspace_id=target["workspace_id"], handoff=doc,
                                                 user_id=ctx["run"]["requested_by"], assertion_key=self.assertion_key)
        except GatewayUnavailable as err:
            if getattr(err, "refused_status", None):
                self._set_handoff(lease, doc["id"], "failed", None)
                return self._finish(lease, "handoff_failed", f"mos_refused_{err.refused_status}",
                                    {"message": str(err)[:300]}, patch, None)
            if ctx["work"]["attempt_count"] >= ctx["work"]["max_attempts"]:
                self._set_handoff(lease, doc["id"], "failed", None)       # bounded retries spent; never `received`
            raise Retryable("upstream_unavailable", str(err)[:200]) from None
        receipt = reply.get("receipt") if isinstance(reply, dict) and "schema_version" not in reply else reply
        if not isinstance(receipt, dict) or receipt.get("schema_version") != "cs-mos-ingestion-receipt-v1":
            return self._finish(lease, "handoff_offered", "awaiting_mos_receipt",
                                {"mos_status": (reply or {}).get("status") if isinstance(reply, dict) else None},
                                patch, None)
        try:
            verify_ingestion_receipt(receipt, doc, [export])
        except CompositionError as err:
            self._set_handoff(lease, doc["id"], "needs_reconciliation", None)
            return self._finish(lease, "handoff_failed", f"receipt_invalid:{err.code}", {"message": err.message[:300]},
                                patch, None)
        self._set_handoff(lease, doc["id"], "received", receipt)
        return self._finish(lease, "handoff_delivered", None, {"receipt_sha256": receipt["sha256"]}, patch, None)

    def _set_handoff(self, lease, handoff_id, state, receipt) -> None:
        with self._tx() as cur:
            cur.execute("select public.cs_post_record_handoff(%s,%s,%s,%s,%s,%s::jsonb)",
                        (*lease, handoff_id, state, json.dumps(receipt) if receipt is not None else None))


class _LeaseExportStore:
    """ExportStore bound to the publish_export lease: asset version row first (lease checked), then the bytes at the exact
    key, then read-back from storage."""

    def __init__(self, rt: PostRunRuntime, lease):
        self.rt, self.lease, self.keys = rt, lease, {}

    def persist(self, *, workspace_id, data, mime_type, name, width, height, rights, parent_assets):
        digest = hashlib.sha256(data).hexdigest()
        with self.rt._tx() as cur:
            cur.execute("select * from public.cs_commit_asset_version(%s,%s,%s,null,%s,'export',%s,%s,%s,%s,%s,null,"
                        "%s::jsonb,%s)",
                        (*self.lease, name, mime_type, len(data), digest, width, height, json.dumps(parent_assets),
                         self.rt.settings.runtime_principal))
            row = dict(cur.fetchone())
        try:
            self.rt.store.put(row["storage_key"], data, mime_type)
        except Exception as exc:
            raise Retryable("store_unavailable", type(exc).__name__) from None
        ref = {"authority": "creative-studio", "workspace_id": workspace_id, "asset_id": str(row["asset_id"]),
               "asset_version_id": str(row["asset_version_id"]), "revision": row["revision"], "sha256": digest,
               "mime_type": mime_type}
        self.keys[ref["asset_version_id"]] = row["storage_key"]
        return {"ref": ref, "name": name, "size_bytes": len(data), "width_px": width, "height_px": height,
                "duration_ms": None, "rights": rights, "origin": "derived", "source_url": None, "captured_at": None,
                "parent_assets": parent_assets}

    def read(self, ref):
        key = self.keys.get(ref["asset_version_id"])
        try:
            return self.rt.store.get(key) if key else None
        except Exception:
            return None
