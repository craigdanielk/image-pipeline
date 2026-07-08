#!/usr/bin/env python3
"""Local operator UI — compile, execute, view job output images.

Core compiler stays dep-free; this UI server has its own requirements (ui/requirements.txt).

  python3 -m pip install -r ui/requirements.txt
  python ui/server.py

Environment:
  IMAGE_PIPELINE_UI_PORT     — default 8787
  IMAGE_PIPELINE_JOBS_ROOT   — extra job scan root (e.g. tenants/joga/.../jobs)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "core"
TOOLS = ROOT / "tools"
UI_DIR = Path(__file__).resolve().parent
UI_STATIC = UI_DIR / "static"
UI_TEMPLATES = UI_DIR / "templates"
KNOWLEDGE = ROOT / "knowledge"
_SKILL_DOC_PATH = ROOT / "SKILL.md"

if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))


def _load_env_file(path: Path) -> None:
    """Minimal .env loader — no python-dotenv dependency (mirrors core/tracking.py)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env_file(ROOT / ".env")
_load_env_file(UI_DIR / ".env")

import artifact_registry  # noqa: E402
import brief_drafter  # noqa: E402
import compile_bundle  # noqa: E402
import graph_to_reactflow  # noqa: E402
import job_compiler  # noqa: E402
import knowledge_store  # noqa: E402
import run_graph  # noqa: E402
import spec_extractor  # noqa: E402
import spec_validator_v2  # noqa: E402
import tenants  # noqa: E402
import workspace_runs  # noqa: E402

app = FastAPI(title="Creative Compiler UI", version="0.3.0")

# In-process registry for POST /api/graph-runs — deliberately not backed by
# graph_persistence's optional Supabase tracking (off by default locally, see
# core/graph_persistence.py). Keyed by a locally-generated run id so the
# live-status API works with zero external config. graph_persistence writes
# still happen underneath (via run_graph_async's track/graph_run_id args)
# when Supabase IS configured — this registry is the guaranteed-available
# layer on top.
_graph_runs: dict[str, dict[str, Any]] = {}


def _graph_run_status_event(run_id: str, event: dict[str, Any]) -> None:
    """on_status callback for run_graph_async — updates the snapshot and
    fans the event out to any live SSE subscribers for this run."""
    entry = _graph_runs.get(run_id)
    if entry is None:
        return
    node_id = event.get("node_id")
    if node_id is None:
        entry["status"] = event.get("status", entry["status"])
    else:
        entry["nodes"][node_id] = {
            "status": event.get("status"),
            "node_type": event.get("node_type"),
        }
    for queue in entry["subscribers"]:
        queue.put_nowait(event)


def _allowed_roots() -> list[Path]:
    roots = [ROOT.resolve()]
    extra = os.environ.get("IMAGE_PIPELINE_JOBS_ROOT", "").strip()
    if extra:
        roots.append(Path(extra).expanduser().resolve())
    return roots


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _resolve_path(path_str: str) -> Path:
    """Resolve path if under any allowed root."""
    raw = Path(unquote(path_str))
    candidate = raw if raw.is_absolute() else (ROOT / raw)
    resolved = candidate.resolve()
    for root in _allowed_roots():
        if _is_under(resolved, root):
            return resolved
    raise HTTPException(status_code=400, detail="path outside allowed workspace roots")


def _humanize_id(job_id: str) -> str:
    """Turn slug id into friendly label: primikey-usba-plate-plan → Plate Plan."""
    # Strip common product prefix if present
    text = job_id
    for prefix in ("plate-", "scene-", "card-", "profile-"):
        if prefix in text:
            idx = text.rfind(prefix)
            if idx >= 0:
                text = text[idx:]
                break
    words = re.split(r"[-_]+", text)
    # Drop trailing numeric-only tokens like "1", "2"
    while words and words[-1].isdigit():
        words.pop()
    if not words:
        words = re.split(r"[-_]+", job_id)
    return " ".join(w.capitalize() for w in words if w)


def _product_from_job_path(path_field: str, jobs_dir_name: str = "jobs") -> str:
    """Extract product slug from job path: the job file's own containing folder
    (…/jobs/primikey-usba/foo.job.json → primikey-usba, however deep the job sits)."""
    parent_name = Path(path_field).parent.name
    if not parent_name or parent_name == jobs_dir_name:
        return "_general"
    return parent_name


def _env_jobs_root() -> Path:
    extra = os.environ.get("IMAGE_PIPELINE_JOBS_ROOT", "").strip()
    if extra:
        p = Path(extra).expanduser().resolve()
        if p.is_dir():
            return p
    return (ROOT / "jobs").resolve()


def _primary_jobs_dir(tenant: str | None = None) -> Path:
    return tenants.resolve_jobs_root(ROOT, tenant, _env_jobs_root())


def _glob_jobs(tenant: str | None = None) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []

    scan_dirs: list[tuple[str, Path]] = [("repo", ROOT / "jobs")]
    if tenant:
        scan_dirs = [("external", _primary_jobs_dir(tenant))]
    else:
        jobs_root = os.environ.get("IMAGE_PIPELINE_JOBS_ROOT", "").strip()
        if jobs_root:
            scan_dirs.append(("external", Path(jobs_root).expanduser().resolve()))

    for source, jobs_dir in scan_dirs:
        if not jobs_dir.is_dir():
            continue
        for path in sorted(jobs_dir.rglob("*.job.json")):
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                rel = path.relative_to(ROOT).as_posix()
                path_field = rel
            except ValueError:
                rel = path.as_posix()
                path_field = rel
            name = path.name
            if name.endswith(".job.json"):
                job_id = name[: -len(".job.json")]
            else:
                job_id = path.stem
            product = _product_from_job_path(path_field)
            out.append(
                {
                    "id": job_id,
                    "label": _humanize_id(job_id),
                    "product": product,
                    "path": path_field,
                    "dir": path.parent.as_posix(),
                    "source": source,
                }
            )
    return out


def _glob_outputs(job_dir: str) -> list[dict[str, Any]]:
    base = _resolve_path(job_dir)
    images: list[dict[str, Any]] = []
    scan_dirs = [base / "out", base / "out" / "webp", base]
    for folder in scan_dirs:
        if not folder.is_dir():
            continue
        for img in sorted(folder.iterdir()):
            if not img.is_file() or img.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            rel = img.as_posix()
            images.append(
                {
                    "name": img.name,
                    "path": rel,
                    "url": f"/media?path={rel}",
                    "mtime": img.stat().st_mtime,
                }
            )
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in sorted(images, key=lambda x: x["mtime"], reverse=True):
        if item["path"] in seen:
            continue
        seen.add(item["path"])
        unique.append(item)
    return unique


def _run_core(script: str, *args: str) -> dict[str, Any]:
    cmd = [sys.executable, str(CORE / script), *args]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail={
                "command": " ".join(cmd),
                "stderr": proc.stderr[-4000:],
                "stdout": proc.stdout[-2000:],
            },
        )
    return {"stdout": proc.stdout, "stderr": proc.stderr}


def _compile_dir_for_job(job_path: Path, job: dict[str, Any]) -> tuple[str, Path]:
    job_id = job.get("id") or job_path.stem
    compile_abs = job_path.parent / "compile" / job_id
    compile_abs.mkdir(parents=True, exist_ok=True)
    return compile_abs.as_posix(), compile_abs


class CompileRequest(BaseModel):
    job: str = Field(..., description="Path to .job.json (repo-relative or absolute under JOBS_ROOT)")
    dry_run: bool = True


class ExecuteRequest(BaseModel):
    job: Optional[str] = None
    compile_dir: Optional[str] = None
    dry_run: bool = True


class CreateJobRequest(BaseModel):
    product_slug: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]*$")
    shot_id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]*$")
    template_id: Optional[str] = None
    subject: dict[str, Any] = Field(default_factory=dict)
    prompt_template: str = Field(
        default="Studio product render — resolved by compiler from campaign.",
    )
    reference_paths: Optional[list[str]] = None
    output: Optional[dict[str, Any]] = None
    engine: Optional[dict[str, Any]] = None
    cad: Optional[str] = None


class GenerateIntentRequest(BaseModel):
    product_slug: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]*$")
    campaign_id: str
    output_types: list[str] = Field(..., min_length=1)
    changes: list[str] = Field(default_factory=list)
    dry_run: bool = True


class CreateProductRequest(BaseModel):
    id: Optional[str] = None
    name: str = Field(..., min_length=1)
    geometry_description: str = Field(..., min_length=1)
    material_color: str = "neutral"
    material_type: str = "generic"


class CreateCampaignRequest(BaseModel):
    id: Optional[str] = None
    product_spec_id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]*$")
    label: str = Field(..., min_length=1)
    mood: str = "studio"
    style: str = "product_hero"
    scene_description: str = Field(..., min_length=1)
    lighting: str = "soft_key_fill, neutral backdrop"
    camera: str = "eye_level, product legible"
    background: str = "neutral_studio"
    composition: str = "product centered, clean negative space"


class ApprovalRequest(BaseModel):
    status: str = Field(..., pattern=r"^(pending|approved|rejected)$")


class ArtifactCommentRequest(BaseModel):
    comment: Optional[str] = None
    drive_url: Optional[str] = None


class BriefDraftRequest(BaseModel):
    product_slug: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]*$")
    tenant: Optional[str] = None
    brief_text: str = Field(..., min_length=1)
    mood: Optional[str] = None
    feedback: Optional[str] = None


_OUTPUT_TEMPLATE_MAP = {
    "pdp": "studio-plate",
    "hero": "hero-scene",
    "lifestyle": "hero-scene",
    "social": "hero-scene",
}


def _knowledge(tenant: str | None = None) -> knowledge_store.KnowledgeStore:
    base = workspace_runs.workspace_dir(ROOT, tenant) if tenant else ROOT
    return knowledge_store.KnowledgeStore(base)


def _workspace_id(tenant: str | None = None) -> str:
    if tenant:
        return tenant
    return os.environ.get("IMAGE_PIPELINE_WORKSPACE_ID", "default").strip() or "default"


def _shots_for_product(slug: str, tenant: str | None = None) -> list[dict[str, Any]]:
    shots: list[dict[str, Any]] = []
    for job in _glob_jobs(tenant):
        if job["product"] == slug:
            shots.append(
                {
                    "id": job["id"],
                    "label": job["label"],
                    "path": job["path"],
                    "dir": job["dir"],
                }
            )
    return shots


def _load_knowledge_campaign(campaign_id: str) -> dict[str, Any]:
    for path in [KNOWLEDGE / "campaigns" / f"{campaign_id}.json"]:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    camp_dir = KNOWLEDGE / "campaigns"
    if camp_dir.is_dir():
        for path in camp_dir.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("id") == campaign_id or path.stem == campaign_id:
                return data
    raise HTTPException(status_code=404, detail=f"campaign not found: {campaign_id}")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "root": str(ROOT),
        "api_version": "0.4.0-greenfield",
    }


@app.get("/api/config")
def config() -> dict[str, Any]:
    jobs_root = os.environ.get("IMAGE_PIPELINE_JOBS_ROOT", "").strip()
    return {
        "workspace": str(ROOT),
        "jobs_root": jobs_root or None,
        "allowed_roots": [str(r) for r in _allowed_roots()],
        "port": int(os.environ.get("IMAGE_PIPELINE_UI_PORT", "8787")),
    }


@app.get("/api/tenants")
def list_tenants_api() -> dict[str, Any]:
    return {"tenants": tenants.load_tenants(ROOT)}


@app.post("/api/tenants")
def add_tenant_api(slug: str, jobs_root: str, label: Optional[str] = None) -> dict[str, Any]:
    entry = tenants.add_tenant(ROOT, slug, jobs_root, label)
    return {"ok": True, "tenant": entry}


@app.get("/api/jobs")
def list_jobs(tenant: Optional[str] = None) -> dict[str, Any]:
    return {"jobs": _glob_jobs(tenant)}


@app.get("/api/products")
def list_products(tenant: Optional[str] = None) -> dict[str, Any]:
    """Knowledge-first products with optional job shot counts."""
    store = _knowledge(tenant)
    products: list[dict[str, Any]] = []
    for row in store.list_products():
        slug = row["slug"]
        products.append(
            {
                "slug": slug,
                "name": row["name"],
                "geometry_description": row.get("geometry_description"),
                "reference_count": row.get("reference_count", 0),
                "shots": _shots_for_product(slug, tenant),
                "source": "knowledge",
            }
        )
    known = {p["slug"] for p in products}
    for job in _glob_jobs(tenant):
        slug = job["product"]
        if slug in known or slug == "_general":
            continue
        known.add(slug)
        products.append(
            {
                "slug": slug,
                "name": slug.replace("-", " ").title(),
                "shots": _shots_for_product(slug, tenant),
                "source": "jobs_only",
            }
        )
    products.sort(key=lambda p: p["name"].lower())
    return {"products": products}


@app.get("/api/workspace")
def get_workspace(tenant: Optional[str] = None) -> dict[str, Any]:
    wid = _workspace_id(tenant)
    meta = workspace_runs.workspace_meta(ROOT, wid)
    products = len(_knowledge(tenant).list_products())
    campaigns = len(_knowledge(tenant).list_campaigns())
    runs = workspace_runs.list_runs(ROOT, wid, limit=100)
    return {
        "workspace": meta,
        "stats": {
            "products": products,
            "campaigns": campaigns,
            "runs": len(runs),
            "runs_active": sum(1 for r in runs if r.get("status") in ("queued", "running")),
        },
    }


@app.get("/api/products/{slug}")
def get_product_unified(slug: str, tenant: Optional[str] = None) -> dict[str, Any]:
    """Knowledge ProductSpec if one exists, else a synthesized jobs_only view.

    Most real products (any tenant that hasn't run the greenfield product wizard)
    only exist as job folders on disk — they never get a knowledge/products/*.json
    entry. Detail pages must still be able to open them.
    """
    try:
        spec = _knowledge(tenant).get_product(slug)
        return {"product": spec, "source": "knowledge"}
    except knowledge_store.KnowledgeError:
        pass
    shots = _shots_for_product(slug, tenant)
    if not shots:
        raise HTTPException(status_code=404, detail=f"product not found: {slug}")
    return {
        "product": {"id": slug, "name": slug.replace("-", " ").title(), "shots": shots},
        "source": "jobs_only",
    }


@app.get("/api/knowledge/products")
def knowledge_list_products(tenant: Optional[str] = None) -> dict[str, Any]:
    return {"products": _knowledge(tenant).list_products()}


@app.get("/api/knowledge/products/{slug}")
def knowledge_get_product(slug: str, tenant: Optional[str] = None) -> dict[str, Any]:
    try:
        return {"product": _knowledge(tenant).get_product(slug)}
    except knowledge_store.KnowledgeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/knowledge/products")
def knowledge_create_product(req: CreateProductRequest, tenant: Optional[str] = None) -> dict[str, Any]:
    try:
        row = _knowledge(tenant).create_product(req.model_dump())
        return {"ok": True, "product": row}
    except knowledge_store.KnowledgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/knowledge/products/{slug}")
def knowledge_update_product(slug: str, body: dict[str, Any], tenant: Optional[str] = None) -> dict[str, Any]:
    try:
        if body.get("id") and body["id"] != slug:
            raise HTTPException(status_code=400, detail="id mismatch")
        body["id"] = slug
        row = _knowledge(tenant).save_product(body)
        return {"ok": True, "product": row}
    except knowledge_store.KnowledgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except spec_validator_v2.SpecV2Error as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/knowledge/products/{slug}/refs")
async def knowledge_upload_product_ref(
    slug: str,
    role: str = Query("product_ref"),
    ref_id: str = Query("ref"),
    tenant: Optional[str] = None,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="missing filename")
    store = _knowledge(tenant)
    try:
        store.get_product(slug)
    except knowledge_store.KnowledgeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", file.filename).strip("-") or "upload.png"
    refs_dir = store.product_refs_dir(slug)
    dest = refs_dir / safe_name
    content = await file.read()
    dest.write_bytes(content)
    try:
        rel = dest.relative_to(ROOT).as_posix()
    except ValueError:
        rel = dest.as_posix()
    asset_path = rel
    try:
        store.add_product_reference(slug, ref_id=ref_id, role=role, asset_path=asset_path)
    except knowledge_store.KnowledgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "path": rel,
        "url": f"/media?path={rel}",
        "asset": asset_path,
        "ref_id": ref_id,
    }


@app.post("/api/knowledge/products/extract")
async def knowledge_extract_product(
    name: str = Form(""),
    notes: str = Form(""),
    photos: list[UploadFile] = File(...),
) -> dict[str, Any]:
    if not photos:
        raise HTTPException(status_code=400, detail="at least one photo is required")
    with tempfile.TemporaryDirectory(prefix="pim-extract-") as tmp:
        photo_paths: list[str] = []
        for i, photo in enumerate(photos):
            if not photo.filename:
                continue
            safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", photo.filename).strip("-") or f"photo-{i}.png"
            dest = Path(tmp) / f"{i:02d}-{safe}"
            dest.write_bytes(await photo.read())
            photo_paths.append(str(dest))
        if not photo_paths:
            raise HTTPException(status_code=400, detail="no valid photo files uploaded")
        try:
            draft = await spec_extractor.extract_product_spec_from_photos(
                photo_paths, name_hint=name or None, notes=notes or None
            )
        except spec_extractor.SpecExtractError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"draft": draft}


@app.get("/api/knowledge/campaigns")
def knowledge_list_campaigns(product_spec_id: Optional[str] = None, tenant: Optional[str] = None) -> dict[str, Any]:
    return {"campaigns": _knowledge(tenant).list_campaigns(product_spec_id)}


@app.get("/api/knowledge/campaigns/{campaign_id}")
def knowledge_get_campaign(campaign_id: str, tenant: Optional[str] = None) -> dict[str, Any]:
    try:
        return {"campaign": _knowledge(tenant).get_campaign(campaign_id)}
    except knowledge_store.KnowledgeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/knowledge/campaigns")
def knowledge_create_campaign(req: CreateCampaignRequest, tenant: Optional[str] = None) -> dict[str, Any]:
    try:
        row = _knowledge(tenant).create_campaign(req.model_dump())
        return {"ok": True, "campaign": row}
    except knowledge_store.KnowledgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/runs/registry")
def list_run_registry(limit: int = 50) -> dict[str, Any]:
    wid = _workspace_id()
    return {"runs": workspace_runs.list_runs(ROOT, wid, limit=limit)}


@app.get("/api/runs/registry/{run_id}")
def get_run_registry(run_id: str) -> dict[str, Any]:
    try:
        return {"run": workspace_runs.get_run(ROOT, run_id, _workspace_id())}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/api/assets/approval")
def patch_asset_approval(req: ApprovalRequest, path: str = Query(...)) -> dict[str, Any]:
    workspace_runs.set_approval(ROOT, path, req.status, _workspace_id())
    return {"ok": True, "path": path, "status": req.status}


@app.get("/api/assets/approvals")
def get_asset_approvals() -> dict[str, Any]:
    return {"approvals": workspace_runs.load_approvals(ROOT, _workspace_id())}


@app.get("/api/artifacts")
def list_artifacts_api(
    product_slug: Optional[str] = None,
    campaign_slug: Optional[str] = None,
    tenant: Optional[str] = None,
    limit: int = 24,
    offset: int = 0,
    include_deleted: bool = False,
    status: Optional[str] = None,
    sync: bool = False,
) -> dict[str, Any]:
    wid = _workspace_id(tenant)
    if sync:
        artifact_registry.sync_registry(ROOT, _primary_jobs_dir(tenant), wid, product_slug=product_slug)
    capped_limit = min(limit, 60)
    filter_kwargs = dict(
        workspace_id=wid,
        product_slug=product_slug,
        campaign_slug=campaign_slug,
        include_deleted=include_deleted,
        status=status,
    )
    rows = artifact_registry.list_artifacts(ROOT, limit=capped_limit, offset=offset, **filter_kwargs)
    total = artifact_registry.count_artifacts(ROOT, **filter_kwargs)
    return {"artifacts": rows, "total": total, "offset": offset, "limit": capped_limit}


@app.post("/api/artifacts/sync")
def sync_artifacts_api(product_slug: Optional[str] = None, tenant: Optional[str] = None) -> dict[str, Any]:
    stats = artifact_registry.sync_registry(
        ROOT, _primary_jobs_dir(tenant), _workspace_id(tenant), product_slug=product_slug
    )
    return {"ok": True, **stats}


@app.patch("/api/artifacts/{artifact_id}")
def patch_artifact_comment(
    artifact_id: str, req: ArtifactCommentRequest, tenant: Optional[str] = None
) -> dict[str, Any]:
    try:
        row = artifact_registry.update_comment(
            ROOT, artifact_id, req.comment, _workspace_id(tenant), drive_url=req.drive_url
        )
        return {"ok": True, "artifact": row}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/artifacts/{artifact_id}")
def delete_artifact(
    artifact_id: str,
    reason: Optional[str] = Query(None),
    tenant: Optional[str] = None,
) -> dict[str, Any]:
    try:
        row = artifact_registry.soft_delete(
            ROOT, artifact_id, reason=reason, workspace_id=_workspace_id(tenant)
        )
        return {"ok": True, "artifact": row}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/artifacts/{artifact_id}/approve")
def approve_artifact_api(artifact_id: str, tenant: Optional[str] = None) -> dict[str, Any]:
    try:
        row = artifact_registry.approve_artifact(ROOT, artifact_id, workspace_id=_workspace_id(tenant))
        return {"ok": True, "artifact": row}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/artifacts/{artifact_id}/restore")
def restore_artifact_api(artifact_id: str, tenant: Optional[str] = None) -> dict[str, Any]:
    try:
        row = artifact_registry.restore_artifact(ROOT, artifact_id, workspace_id=_workspace_id(tenant))
        return {"ok": True, "artifact": row}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/templates")
def list_templates() -> dict[str, Any]:
    """Return job templates with human labels."""
    templates: list[dict[str, Any]] = []
    if UI_TEMPLATES.is_dir():
        for path in sorted(UI_TEMPLATES.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            templates.append(data)
    return {"templates": templates}


@app.post("/api/briefs/draft")
async def draft_brief(req: BriefDraftRequest) -> dict[str, Any]:
    shots = _shots_for_product(req.product_slug, req.tenant)
    if not shots:
        raise HTTPException(status_code=404, detail=f"no jobs found for product: {req.product_slug}")
    product_dir = Path(shots[0]["dir"])

    cad_path = product_dir / "cad.md"
    cad_text = cad_path.read_text(encoding="utf-8") if cad_path.is_file() else ""

    examples: list[str] = []
    suggested_engine: dict[str, Any] | None = None
    suggested_output: dict[str, Any] | None = None
    for shot in shots[:3]:
        try:
            job = json.loads(Path(shot["path"]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        tpl = (job.get("spec") or {}).get("prompt_template")
        if tpl:
            examples.append(tpl)
        if suggested_engine is None and job.get("engine"):
            suggested_engine = job["engine"]
        if suggested_output is None and job.get("output"):
            suggested_output = {
                k: v for k, v in job["output"].items() if k in ("imgtype", "aspect_ratio", "resolution", "count")
            }

    skill_doc = _SKILL_DOC_PATH.read_text(encoding="utf-8") if _SKILL_DOC_PATH.is_file() else ""

    try:
        draft = await brief_drafter.draft_prompt_template(
            cad_text=cad_text,
            brief_text=req.brief_text,
            mood=req.mood,
            example_prompts=examples,
            feedback=req.feedback,
            system_context=skill_doc,
        )
    except brief_drafter.BriefDraftError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "prompt_template": draft,
        "cad": "cad.md" if cad_path.is_file() else None,
        "examples_used": len(examples),
        "suggested_engine": suggested_engine,
        "suggested_output": suggested_output,
    }


@app.get("/api/products/{slug}/references")
def list_product_references(slug: str, tenant: Optional[str] = None) -> dict[str, Any]:
    product_dir = _resolve_product_dir(slug, tenant)
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    # Two conventions coexist across tenants: this repo's own jobs use "reference/",
    # joga's real production jobs use "refs/". Scan both.
    for dirname in ("reference", "refs"):
        ref_dir = product_dir / dirname
        if not ref_dir.is_dir():
            continue
        for img in sorted(ref_dir.rglob("*")):
            if not img.is_file() or img.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            path_str = img.as_posix()
            if path_str in seen:
                continue
            seen.add(path_str)
            refs.append({"path": path_str, "name": img.name, "url": f"/media?path={path_str}"})
    return {"references": refs}


def _resolve_product_dir(product_slug: str, tenant: str | None) -> Path:
    """Existing products may sit under a campaign folder (jobs/<campaign>/<product>/) —
    reuse an existing shot's directory rather than assuming a flat jobs/<product>/ layout."""
    existing = _shots_for_product(product_slug, tenant)
    if existing:
        return Path(existing[0]["dir"])
    return _primary_jobs_dir(tenant) / product_slug


@app.post("/api/jobs/create")
def create_job(req: CreateJobRequest, tenant: Optional[str] = None) -> dict[str, Any]:
    """Write a new .job.json and create supporting directories."""
    product_dir = _resolve_product_dir(req.product_slug, tenant)
    product_dir.mkdir(parents=True, exist_ok=True)
    (product_dir / "reference").mkdir(exist_ok=True)
    (product_dir / "out").mkdir(exist_ok=True)

    job_path = product_dir / f"{req.shot_id}.job.json"
    if job_path.exists():
        raise HTTPException(status_code=409, detail=f"Shot already exists: {req.shot_id}")

    defaults: dict[str, Any] = {}
    if req.template_id and UI_TEMPLATES.is_dir():
        tpl_path = UI_TEMPLATES / f"{req.template_id}.json"
        if tpl_path.is_file():
            tpl = json.loads(tpl_path.read_text(encoding="utf-8"))
            defaults = tpl.get("defaults") or {}

    spec: dict[str, Any] = dict(defaults.get("spec") or {})
    spec["prompt_template"] = req.prompt_template
    if req.cad:
        spec["cad"] = req.cad
    elif (product_dir / "cad.md").is_file():
        spec["cad"] = "cad.md"

    subject = {**(defaults.get("subject") or {}), **req.subject}
    output = req.output if req.output is not None else defaults.get("output")
    engine = req.engine if req.engine is not None else defaults.get("engine")

    reference: Any = defaults.get("reference")
    if req.reference_paths:
        reference = req.reference_paths[0] if len(req.reference_paths) == 1 else req.reference_paths

    schema_rel = "../../core/job.schema.json"
    try:
        depth = len(job_path.relative_to(ROOT).parts) - 1
        schema_rel = "../" * depth + "core/job.schema.json"
    except ValueError:
        schema_rel = "../core/job.schema.json"

    job_doc: dict[str, Any] = {
        "$schema": schema_rel,
        "schema_version": "job-v1",
        "id": req.shot_id,
        "subject": subject,
        "spec": spec,
        "reference": reference,
        "output": output,
        "engine": engine,
    }

    job_path.write_text(json.dumps(job_doc, indent=2) + "\n", encoding="utf-8")

    try:
        rel_path = job_path.relative_to(ROOT).as_posix()
    except ValueError:
        rel_path = job_path.as_posix()

    return {
        "ok": True,
        "job": {
            "id": req.shot_id,
            "label": _humanize_id(req.shot_id),
            "product": req.product_slug,
            "path": rel_path,
            "dir": product_dir.as_posix(),
        },
    }


@app.post("/api/upload")
async def upload_reference(
    product_slug: str = Query(..., pattern=r"^[a-z0-9][a-z0-9-]*$"),
    tenant: Optional[str] = None,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Upload a reference image to {product}/reference/ under jobs root."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename required")

    ext = Path(file.filename).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise HTTPException(status_code=400, detail="only image files supported")

    ref_dir = _resolve_product_dir(product_slug, tenant) / "reference"
    ref_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "-", Path(file.filename).name)
    dest = ref_dir / safe_name
    content = await file.read()
    dest.write_bytes(content)

    return {
        "ok": True,
        "filename": safe_name,
        "path": f"reference/{safe_name}",
        "url": f"/media?path={dest.as_posix()}",
        "absolute": dest.as_posix(),
    }


@app.post("/api/compile")
def compile_job(req: CompileRequest) -> dict[str, Any]:
    job_path = _resolve_path(req.job)
    if not job_path.is_file():
        raise HTTPException(status_code=404, detail=f"job not found: {req.job}")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    compile_dir, compile_abs = _compile_dir_for_job(job_path, job)

    args = [job_path.as_posix(), "--compile-dir", compile_dir]
    if req.dry_run:
        args.append("--dry-run")
    _run_core("compile_job.py", *args)

    manifest_path = compile_abs / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    prompt = ""
    plan_path = compile_abs / "ProviderExecutionPlan.json"
    if plan_path.is_file():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        prompt = (plan.get("prompt") or {}).get("positive") or ""

    graph_rf: dict[str, Any] = {}
    graph_path = compile_abs / "graph.json"
    if graph_path.is_file():
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        graph_rf = graph_to_reactflow.graph_v1_to_reactflow(graph)

    return {
        "job_id": job.get("id") or job_path.stem,
        "compile_dir": compile_dir,
        "manifest": manifest,
        "prompt": prompt,
        "graph": graph_rf,
    }


@app.post("/api/execute")
def execute_compile(req: ExecuteRequest) -> dict[str, Any]:
    compile_dir = req.compile_dir
    job_path: Path | None = None

    if req.job:
        job_path = _resolve_path(req.job)
        job = json.loads(job_path.read_text(encoding="utf-8"))
        compile_dir, compile_abs = _compile_dir_for_job(job_path, job)
    elif compile_dir:
        compile_abs = _resolve_path(compile_dir)
    else:
        raise HTTPException(status_code=400, detail="compile_dir or job required")

    if job_path:
        args = [job_path.as_posix(), "--compile-dir", compile_dir]
        if req.dry_run:
            args.append("--dry-run")
        _run_core("compile_job.py", *args)
    elif not (compile_abs / "manifest.json").is_file():
        raise HTTPException(status_code=404, detail="compile directory missing manifest.json")

    args = [compile_dir]
    if req.dry_run:
        args.append("--dry-run")
    out = _run_core("run_graph.py", *args)

    result: dict[str, Any] = {"raw_stdout": out["stdout"]}
    try:
        result["result"] = json.loads(out["stdout"])
    except json.JSONDecodeError:
        pass

    manifest = json.loads((compile_abs / "manifest.json").read_text(encoding="utf-8"))
    source = manifest.get("source") or {}
    source_path = source.get("path") or ""
    job_dir = ""
    if source_path:
        sp = Path(source_path)
        job_dir = sp.parent.as_posix()
    elif job_path:
        job_dir = job_path.parent.as_posix()
    else:
        job_dir = compile_dir.rsplit("/compile/", 1)[0]

    result["outputs"] = _glob_outputs(job_dir) if job_dir else []
    return result


@app.get("/api/outputs")
def list_outputs(job_dir: str) -> dict[str, Any]:
    return {"outputs": _glob_outputs(job_dir)}


@app.get("/api/compile-tree")
def compile_tree(compile_dir: str) -> dict[str, Any]:
    base = _resolve_path(compile_dir)
    if not base.is_dir():
        raise HTTPException(status_code=404, detail="compile dir not found")
    files: list[dict[str, str]] = []
    for path in sorted(base.iterdir()):
        if path.is_file():
            files.append({"name": path.name, "path": path.as_posix()})
    manifest = {}
    mp = base / "manifest.json"
    if mp.is_file():
        manifest = json.loads(mp.read_text(encoding="utf-8"))
    return {"files": files, "manifest": manifest}


@app.get("/api/artifact")
def get_artifact(path: str) -> dict[str, Any]:
    file_path = _resolve_path(path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    if file_path.suffix.lower() not in {".json", ".md"}:
        raise HTTPException(status_code=400, detail="only .json and .md artifacts supported")
    text = file_path.read_text(encoding="utf-8")
    if file_path.suffix.lower() == ".json":
        try:
            return {"path": path, "content": json.loads(text), "raw": text}
        except json.JSONDecodeError:
            pass
    return {"path": path, "content": None, "raw": text}


@app.get("/api/campaigns")
def list_campaigns(tenant: Optional[str] = None) -> dict[str, Any]:
    campaigns: list[dict[str, Any]] = []
    camp_dir = _knowledge(tenant).campaigns_dir
    if camp_dir.is_dir():
        for path in sorted(camp_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            camp = data.get("campaign") or {}
            campaigns.append(
                {
                    "id": data.get("id") or path.stem,
                    "label": camp.get("label") or path.stem.replace("-", " ").title(),
                    "product_spec_id": data.get("product_spec_id"),
                    "scene": data.get("scene"),
                }
            )
    return {"campaigns": campaigns}


@app.get("/api/operations")
def list_operations() -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    ops_dir = KNOWLEDGE / "operations"
    if ops_dir.is_dir():
        for path in sorted(ops_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            operations.append(
                {
                    "id": data.get("id") or path.stem,
                    "label": data.get("label") or path.stem.replace("_", " ").title(),
                }
            )
    return {"operations": operations}


@app.get("/api/dashboard/stats")
def dashboard_stats() -> dict[str, Any]:
    jobs = _glob_jobs()
    products_dir = KNOWLEDGE / "products"
    campaigns_dir = KNOWLEDGE / "campaigns"
    products = len(list(products_dir.glob("*.json"))) if products_dir.is_dir() else 0
    campaigns = len(list(campaigns_dir.glob("*.json"))) if campaigns_dir.is_dir() else 0
    outputs = 0
    credits_estimated = 0
    recent: list[dict[str, Any]] = []

    for job in jobs:
        job_path = _resolve_path(job["path"])
        mtime = job_path.stat().st_mtime if job_path.is_file() else 0
        job_outputs = 0
        try:
            job_outputs = len(_glob_outputs(job["dir"]))
            outputs += job_outputs
        except HTTPException:
            pass
        status = "complete" if job_outputs else "pending"
        credits_estimated += 2 if job_outputs else 0
        recent.append(
            {
                **job,
                "mtime": mtime,
                "status": status,
                "outputs": job_outputs,
            }
        )

    recent.sort(key=lambda r: r["mtime"], reverse=True)
    registry_runs = workspace_runs.list_runs(ROOT, _workspace_id(), limit=8)
    if registry_runs:
        recent_runs = [
            {
                "id": r.get("shot_id", r["id"]),
                "label": r.get("shot_id", r["id"]),
                "product": r.get("product_slug", ""),
                "path": r.get("job_path", ""),
                "mtime": r.get("updated_at", r.get("created_at", 0)),
                "status": r.get("status", "pending"),
                "outputs": len(r.get("outputs") or []),
                "run_id": r["id"],
            }
            for r in registry_runs
        ]
    else:
        recent_runs = recent[:8]
    meta = workspace_runs.workspace_meta(ROOT, _workspace_id())
    return {
        "workspace": meta,
        "products": products,
        "campaigns": campaigns,
        "jobs": len(jobs),
        "outputs": outputs,
        "credits_estimated": credits_estimated,
        "recent_runs": recent_runs,
    }


@app.post("/api/generate/intent")
def generate_intent(req: GenerateIntentRequest) -> dict[str, Any]:
    """Creator Generate — intent only; prompt from template + campaign (never from UI)."""
    campaign = _load_knowledge_campaign(req.campaign_id)
    primary_output = req.output_types[0]
    template_id = _OUTPUT_TEMPLATE_MAP.get(primary_output, "hero-scene")

    tpl_defaults: dict[str, Any] = {}
    tpl_path = UI_TEMPLATES / f"{template_id}.json"
    if tpl_path.is_file():
        tpl_defaults = json.loads(tpl_path.read_text(encoding="utf-8")).get("defaults") or {}

    scene = campaign.get("scene") or {}
    subject = {
        **(tpl_defaults.get("subject") or {}),
        "campaign": (campaign.get("campaign") or {}).get("label") or req.campaign_id,
        "lighting": scene.get("lighting", ""),
        "camera": scene.get("camera", ""),
        "background": scene.get("background", ""),
        "composition": scene.get("composition", ""),
    }
    for change in req.changes:
        subject[f"change_{change}"] = True

    spec = dict(tpl_defaults.get("spec") or {})
    prompt_template = spec.get("prompt_template") or (
        "Studio product photograph for {campaign}. Lighting: {lighting}. Camera: {camera}. "
        "Background: {background}. Composition: {composition}."
    )

    import time

    shot_id = f"{req.product_slug}-{primary_output}-{int(time.time()) % 100000}"

    create_req = CreateJobRequest(
        product_slug=req.product_slug,
        shot_id=shot_id,
        template_id=template_id,
        subject=subject,
        prompt_template=prompt_template,
        output=tpl_defaults.get("output"),
        engine=tpl_defaults.get("engine"),
    )
    created = create_job(create_req)
    job_path = created["job"]["path"]
    job_dir = created["job"]["dir"]

    if req.dry_run:
        compile_abs_parent = _resolve_path(job_dir)
        job_file = _resolve_path(job_path)
        compile_dir = compile_abs_parent / "compile" / shot_id
        compile_dir.mkdir(parents=True, exist_ok=True)
        _run_core(
            "compile_job.py",
            job_file.as_posix(),
            "--compile-dir",
            compile_dir.as_posix(),
            "--dry-run",
        )
        out = _run_core("run_graph.py", compile_dir.as_posix(), "--dry-run")
        credits = None
        try:
            result = json.loads(out["stdout"])
            cost = result.get("cost") or {}
            if isinstance(cost, dict):
                credits = cost.get("credits") or cost.get("estimated_credits")
        except json.JSONDecodeError:
            pass
        return {
            "message": f"Estimate for {shot_id}",
            "credits": credits,
            "job_path": job_path,
        }

    run = workspace_runs.create_run(
        ROOT,
        product_slug=req.product_slug,
        campaign_id=req.campaign_id,
        job_path=job_path,
        job_dir=job_dir,
        shot_id=shot_id,
        output_types=req.output_types,
        changes=req.changes,
        workspace_id=_workspace_id(),
    )
    workspace_runs.update_run(ROOT, run["id"], status="running", workspace_id=_workspace_id())
    try:
        job_file = _resolve_path(job_path)
        compile_dir = job_file.parent / "compile" / shot_id
        compile_dir.mkdir(parents=True, exist_ok=True)
        _run_core(
            "compile_job.py",
            job_file.as_posix(),
            "--compile-dir",
            compile_dir.as_posix(),
        )
        _run_core("run_graph.py", compile_dir.as_posix())
        outputs = _glob_outputs(job_dir)
        workspace_runs.update_run(
            ROOT,
            run["id"],
            status="complete",
            outputs=outputs,
            workspace_id=_workspace_id(),
        )
        return {
            "message": f"Generated {shot_id}",
            "job_path": job_path,
            "compile_dir": compile_dir.as_posix(),
            "run_id": run["id"],
            "outputs": outputs,
        }
    except Exception as exc:
        workspace_runs.update_run(
            ROOT,
            run["id"],
            status="failed",
            error=str(exc),
            workspace_id=_workspace_id(),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/graph-reactflow")
def graph_reactflow(compile_dir: str) -> dict[str, Any]:
    base = _resolve_path(compile_dir)
    graph_path = base / "graph.json"
    if not graph_path.is_file():
        raise HTTPException(status_code=404, detail="graph.json not found")
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    return graph_to_reactflow.graph_v1_to_reactflow(graph)


class BoardOperationRequest(BaseModel):
    op: str
    target: Optional[str] = None
    asset: Optional[str] = None


class BoardCompileRequest(BaseModel):
    product_slug: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]*$")
    campaign_id: str
    output_types: list[str] = Field(..., min_length=1)
    operations: list[BoardOperationRequest] = Field(default_factory=list)
    tenant: Optional[str] = None


@app.post("/api/board/compile")
def compile_board(req: BoardCompileRequest) -> dict[str, Any]:
    """Compile the Board's current product+campaign+operations selection into
    a real multi-node graph-v1 chain, written to a fresh compile_dir. The
    returned compile_dir is what POST /api/graph-runs (existing, unmodified)
    runs/re-runs against, including target_node_id-scoped re-run."""
    store = _knowledge(req.tenant)
    try:
        product_spec = store.get_product(req.product_slug)
    except knowledge_store.KnowledgeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        campaign_spec = store.get_campaign(req.campaign_id)
    except knowledge_store.KnowledgeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    for op_req in req.operations:
        if op_req.op not in spec_validator_v2._VALID_OPS:
            raise HTTPException(status_code=400, detail=f"unknown operation: {op_req.op!r}")

    scene = campaign_spec.get("scene") or {}
    job = {
        "schema_version": "job-v1",
        "id": f"board-{req.product_slug}-{uuid.uuid4().hex[:8]}",
        "_dir": str(ROOT),
        "spec": {
            "prompt_template": "{cad}. {composition}. {background}.",
            "cad": product_spec.get("product", {}).get("geometry", {}).get("description", ""),
        },
        "engine": {"name": "higgsfield", "model": "nano_banana_2"},
        "output": {
            "imgtype": req.output_types[0],
            "dir": "out",
            "count": 1,
            "composition": scene.get("composition", ""),
            "background": scene.get("background", ""),
        },
    }

    edit_specs = []
    for i, op_req in enumerate(req.operations):
        # v1 shape (core/spec_validator.py:75-85, EDIT_SCHEMA = "edit-spec-v1") —
        # this is the schema job_compiler.compile_job_multi_op (Task 4) validates
        # against via spec_validator.validate_edit_spec, NOT the newer edit-spec-v2
        # (core/spec_validator_v2.py) used by the knowledge-store CRUD path.
        # Required fields confirmed live: schema_version, id (slug), operation,
        # parent_asset_id. target/asset are carried as extra metadata for the
        # Operation node's own params, not validated by v1's schema check.
        edit = {
            "schema_version": "edit-spec-v1",
            "id": f"{job['id']}-edit-{i}",
            "operation": op_req.op,
            "parent_asset_id": f"{job['id']}-anchor",
            "target": op_req.target,
            "asset": op_req.asset,
            "constraints": [],
            "exclusions": [],
            "provenance": {"compiled_from": "board-ui"},
        }
        edit_specs.append(edit)

    bundle = job_compiler.compile_job_multi_op(job, edit_specs, dry_run=True)

    compile_dir = str(_allowed_roots()[0] / "workspace" / _workspace_id(req.tenant) / "board-compiles" / job["id"])
    compile_bundle.write_compile_dir(compile_dir, bundle, source={"kind": "board-ui", "path": None})

    graph = bundle["graph"]
    return {"compile_dir": compile_dir, "graph": graph_to_reactflow.graph_v1_to_reactflow(graph)}


class GraphRunRequest(BaseModel):
    compile_dir: str
    target_node_id: Optional[str] = None
    dry_run: bool = True


@app.post("/api/graph-runs")
async def create_graph_run(req: GraphRunRequest) -> dict[str, Any]:
    """Start a graph run as a background task — returns immediately.

    Unlike /api/execute (blocks until the whole graph finishes), this
    kicks off run_graph.run_graph_async and lets the caller watch progress
    via GET /api/graph-runs/{id} (snapshot) or .../stream (live SSE)."""
    base = _resolve_path(req.compile_dir)
    graph_path = base / "graph.json"
    if not graph_path.is_file():
        raise HTTPException(status_code=404, detail="graph.json not found")
    graph = json.loads(graph_path.read_text(encoding="utf-8"))

    if req.target_node_id:
        node_ids = {n["id"] for n in graph.get("nodes") or []}
        if req.target_node_id not in node_ids:
            raise HTTPException(
                status_code=400,
                detail=f"target_node_id not in graph: {req.target_node_id!r}",
            )

    run_id = str(uuid.uuid4())
    _graph_runs[run_id] = {"status": "running", "nodes": {}, "subscribers": []}

    async def _execute() -> None:
        try:
            # run_graph_async executes from a *runtime* bundle (resolved refs,
            # provider prompts, etc.), not the bare graph.json — build it here
            # the same way run_graph.run_compile_dir does for the CLI/`/api/execute`
            # path. Without this, every call fails immediately with
            # "runtime_bundle is required — compile before execute."
            runtime_bundle = compile_bundle.load_runtime_bundle(req.compile_dir)
            await run_graph.run_graph_async(
                graph,
                runtime_bundle=runtime_bundle,
                compile_dir=req.compile_dir,
                dry_run=req.dry_run,
                target_node_id=req.target_node_id,
                on_status=lambda event: _graph_run_status_event(run_id, event),
            )
        except Exception as exc:
            _graph_run_status_event(run_id, {"node_id": None, "status": "failed", "error": str(exc)})

    asyncio.create_task(_execute())
    return {"graph_run_id": run_id}


@app.get("/api/graph-runs/{run_id}")
def get_graph_run(run_id: str) -> dict[str, Any]:
    entry = _graph_runs.get(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown graph_run_id")
    return {"graph_run_id": run_id, "status": entry["status"], "nodes": entry["nodes"]}


@app.get("/api/graph-runs/{run_id}/stream")
async def stream_graph_run(run_id: str) -> StreamingResponse:
    entry = _graph_runs.get(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown graph_run_id")

    queue: asyncio.Queue = asyncio.Queue()
    entry["subscribers"].append(queue)

    terminal_statuses = {"completed", "failed", "partial"}

    async def _events():
        try:
            yield f"event: snapshot\ndata: {json.dumps({'status': entry['status'], 'nodes': entry['nodes']})}\n\n"
            if entry["status"] in terminal_statuses:
                return
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("node_id") is None and event.get("status") in terminal_statuses:
                    return
        finally:
            if queue in entry["subscribers"]:
                entry["subscribers"].remove(queue)

    return StreamingResponse(_events(), media_type="text/event-stream")


@app.get("/media")
def serve_media(path: str) -> FileResponse:
    file_path = _resolve_path(path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(file_path)


# Legacy path-style media for backwards compat
@app.get("/media/{file_path:path}")
def serve_media_legacy(file_path: str) -> FileResponse:
    return serve_media(path=file_path)


class SPAStaticFiles(StaticFiles):
    """Serve the built SPA; unknown non-API paths fall back to index.html so a
    hard refresh on a client-side route doesn't surface FastAPI's JSON 404."""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


app.mount("/", SPAStaticFiles(directory=str(UI_STATIC), html=True), name="static")


def main() -> None:
    import uvicorn

    port = int(os.environ.get("IMAGE_PIPELINE_UI_PORT", "8787"))
    jobs_root = os.environ.get("IMAGE_PIPELINE_JOBS_ROOT", "")
    print(f"Creative Compiler UI: http://127.0.0.1:{port}")
    print(f"Workspace: {ROOT}")
    if jobs_root:
        print(f"JOBS_ROOT: {jobs_root}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
