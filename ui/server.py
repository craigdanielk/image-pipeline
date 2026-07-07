#!/usr/bin/env python3
"""Local operator UI — compile, execute, view job output images.

Core compiler stays dep-free; this UI server has its own requirements (ui/requirements.txt).

  pip install -r ui/requirements.txt
  python ui/server.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "core"
UI_STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Creative Compiler UI", version="0.1.0")


def _safe_path(rel: str) -> Path:
    """Resolve path under repo root; reject traversal."""
    candidate = (ROOT / rel).resolve()
    if not str(candidate).startswith(str(ROOT.resolve())):
        raise HTTPException(status_code=400, detail="path escapes workspace")
    return candidate


def _glob_jobs() -> list[dict[str, str]]:
    jobs_dir = ROOT / "jobs"
    out: list[dict[str, str]] = []
    if not jobs_dir.is_dir():
        return out
    for path in sorted(jobs_dir.rglob("*.job.json")):
        rel = path.relative_to(ROOT).as_posix()
        out.append({"id": path.stem, "path": rel, "dir": path.parent.relative_to(ROOT).as_posix()})
    return out


def _glob_outputs(job_rel_dir: str) -> list[dict[str, Any]]:
    base = _safe_path(job_rel_dir)
    images: list[dict[str, Any]] = []
    for folder in [base / "out", base]:
        if not folder.is_dir():
            continue
        for png in sorted(folder.glob("*.png")):
            rel = png.relative_to(ROOT).as_posix()
            images.append(
                {
                    "name": png.name,
                    "path": rel,
                    "url": f"/media/{rel}",
                    "mtime": png.stat().st_mtime,
                }
            )
    # dedupe by path
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


class CompileRequest(BaseModel):
    job: str = Field(..., description="Relative path to .job.json under repo root")
    dry_run: bool = True


class ExecuteRequest(BaseModel):
    job: str | None = None
    compile_dir: str | None = None
    dry_run: bool = True


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "root": str(ROOT)}


@app.get("/api/jobs")
def list_jobs() -> dict[str, Any]:
    return {"jobs": _glob_jobs()}


@app.post("/api/compile")
def compile_job(req: CompileRequest) -> dict[str, Any]:
    job_path = _safe_path(req.job)
    if not job_path.is_file():
        raise HTTPException(status_code=404, detail=f"job not found: {req.job}")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job_id = job.get("id") or job_path.stem
    job_dir = job_path.parent.relative_to(ROOT).as_posix()
    compile_dir = f"{job_dir}/compile/{job_id}"
    compile_abs = _safe_path(compile_dir)
    compile_abs.mkdir(parents=True, exist_ok=True)

    args = [req.job, "--compile-dir", compile_dir]
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

    return {
        "job_id": job_id,
        "compile_dir": compile_dir,
        "manifest": manifest,
        "prompt": prompt,
    }


@app.post("/api/execute")
def execute_compile(req: ExecuteRequest) -> dict[str, Any]:
    compile_dir = req.compile_dir
    job_rel = req.job

    if job_rel and not compile_dir:
        job_path = _safe_path(job_rel)
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job_id = job.get("id") or job_path.stem
        job_dir = job_path.parent.relative_to(ROOT).as_posix()
        compile_dir = f"{job_dir}/compile/{job_id}"

    if not compile_dir:
        raise HTTPException(status_code=400, detail="compile_dir or job required")

    compile_abs = _safe_path(compile_dir)
    compile_abs.mkdir(parents=True, exist_ok=True)

    if job_rel:
        args = [job_rel, "--compile-dir", compile_dir]
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
        try:
            job_dir = sp.parent.relative_to(ROOT).as_posix()
        except ValueError:
            job_dir = compile_dir.rsplit("/compile/", 1)[0]
    else:
        job_dir = compile_dir.rsplit("/compile/", 1)[0]

    result["outputs"] = _glob_outputs(job_dir) if job_dir else []
    return result


@app.get("/api/outputs")
def list_outputs(job_dir: str) -> dict[str, Any]:
    return {"outputs": _glob_outputs(job_dir)}


@app.get("/api/compile-tree")
def compile_tree(compile_dir: str) -> dict[str, Any]:
    base = _safe_path(compile_dir)
    if not base.is_dir():
        raise HTTPException(status_code=404, detail="compile dir not found")
    files: list[dict[str, str]] = []
    for path in sorted(base.iterdir()):
        if path.is_file():
            files.append({"name": path.name, "path": path.relative_to(ROOT).as_posix()})
    manifest = {}
    mp = base / "manifest.json"
    if mp.is_file():
        manifest = json.loads(mp.read_text(encoding="utf-8"))
    return {"files": files, "manifest": manifest}


@app.get("/media/{file_path:path}")
def serve_media(file_path: str) -> FileResponse:
    path = _safe_path(file_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path)


app.mount("/", StaticFiles(directory=str(UI_STATIC), html=True), name="static")


def main() -> None:
    import uvicorn

    port = int(os.environ.get("IMAGE_PIPELINE_UI_PORT", "8787"))
    print(f"Creative Compiler UI: http://127.0.0.1:{port}")
    print(f"Workspace: {ROOT}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
