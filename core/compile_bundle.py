#!/usr/bin/env python3
"""Compile bundle — manifest contract for compile/ artifact directories.

The runtime never compiles. It loads runtime_bundle via manifest pointers only.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from typing import Any

MANIFEST_SCHEMA = "compile-manifest-v1"
RUNTIME_BUNDLE_SCHEMA = "runtime-bundle-v1"
LOWERING_VERSION = "job-v1@1"

FILE_PRODUCT = "ProductSpec.json"
FILE_CREATIVE = "CreativeSpec.json"
FILE_EDITS = "EditSpecs.json"
FILE_EXECUTION = "ExecutionRequest.json"
FILE_PROMPT = "ProviderPrompt.json"
FILE_PLAN = "ProviderExecutionPlan.json"
FILE_GRAPH = "graph.json"
FILE_RUNTIME_CTX = "RuntimeContext.json"
FILE_MANIFEST = "manifest.json"

_COMPILER_FILES = (
    FILE_PRODUCT,
    FILE_CREATIVE,
    FILE_EDITS,
    FILE_EXECUTION,
    FILE_PROMPT,
    FILE_PLAN,
    FILE_GRAPH,
    FILE_RUNTIME_CTX,
)

_RUNTIME_FILES = (
    FILE_GRAPH,
    FILE_PLAN,
    FILE_EXECUTION,
    FILE_RUNTIME_CTX,
    FILE_MANIFEST,
)

_FORBIDDEN_RUNTIME_KEYS = frozenset(
    {
        "product_spec",
        "creative_spec",
        "edit_specs",
        "provider_prompt",
    }
)


class CompileBundleError(ValueError):
    """Raised when compile directory contract is violated."""


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash_obj(obj: Any) -> str:
    return content_hash_bytes(_canonical_json(obj).encode("utf-8"))


def is_compile_dir(path: str) -> bool:
    return os.path.isfile(os.path.join(path, FILE_MANIFEST))


def _write_json(path: str, obj: Any) -> str:
    payload = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(payload)
    return content_hash_bytes(payload.encode("utf-8"))


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_manifest(
    *,
    job_id: str,
    dry_run: bool,
    source: dict[str, Any],
    artifact_hashes: dict[str, dict[str, str]],
    compile_hash: str,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA,
        "job_id": job_id,
        "lowering_version": LOWERING_VERSION,
        "dry_run": dry_run,
        "source": source,
        "artifacts": artifact_hashes,
        "compile_hash": compile_hash,
    }
    unsigned = copy.deepcopy(manifest)
    manifest["manifest_hash"] = content_hash_obj(unsigned)
    return manifest


def validate_manifest(manifest: dict[str, Any], compile_dir: str) -> None:
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise CompileBundleError(f"Expected {MANIFEST_SCHEMA!r} manifest.")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise CompileBundleError("manifest.artifacts must be an object.")
    for name, meta in artifacts.items():
        path = os.path.join(compile_dir, name)
        if not os.path.isfile(path):
            raise CompileBundleError(f"Manifest references missing file: {name}")
        with open(path, encoding="utf-8") as f:
            on_disk = content_hash_bytes(f.read().encode("utf-8"))
        expected = (meta or {}).get("content_hash")
        if expected != on_disk:
            raise CompileBundleError(
                f"Hash mismatch for {name}: manifest={expected!r} disk={on_disk!r}"
            )


def write_compile_dir(
    compile_dir: str,
    bundle: dict[str, Any],
    *,
    source: dict[str, Any],
) -> dict[str, Any]:
    """Write full compile/ directory. Returns manifest."""
    os.makedirs(compile_dir, exist_ok=True)

    edits = bundle.get("edit_specs") or []
    runtime_ctx = {
        "schema_version": "runtime-context-v1",
        "job_id": bundle["job_id"],
        "job_dir": bundle.get("job_dir", "."),
        "persist_context": bundle.get("persist_context") or {},
    }

    payloads: dict[str, Any] = {
        FILE_PRODUCT: bundle["product_spec"],
        FILE_CREATIVE: bundle["creative_spec"],
        FILE_EDITS: edits,
        FILE_EXECUTION: bundle["execution_request"],
        FILE_PROMPT: bundle["provider_prompt"],
        FILE_PLAN: bundle["execution_plan"],
        FILE_GRAPH: bundle["graph"],
        FILE_RUNTIME_CTX: runtime_ctx,
    }

    artifact_hashes: dict[str, dict[str, str]] = {}
    for filename, obj in payloads.items():
        path = os.path.join(compile_dir, filename)
        file_hash = _write_json(path, obj)
        schema_ver = obj.get("schema_version") if isinstance(obj, dict) else None
        entry: dict[str, str] = {"content_hash": file_hash}
        if schema_ver:
            entry["schema_version"] = schema_ver
        artifact_hashes[filename] = entry

    compile_hash = bundle.get("compile_hash") or bundle.get("provider_prompt", {}).get(
        "metadata", {}
    ).get("content_hash", "")
    if not compile_hash:
        raise CompileBundleError("bundle missing compile_hash / provider_prompt hash.")

    manifest = build_manifest(
        job_id=bundle["job_id"],
        dry_run=bool(bundle.get("execution_request", {}).get("dry_run")),
        source=source,
        artifact_hashes=artifact_hashes,
        compile_hash=compile_hash,
    )
    manifest_path = os.path.join(compile_dir, FILE_MANIFEST)
    _write_json(manifest_path, manifest)
    return manifest


def load_manifest(compile_dir: str) -> dict[str, Any]:
    path = os.path.join(compile_dir, FILE_MANIFEST)
    if not os.path.isfile(path):
        raise CompileBundleError(f"No manifest at {compile_dir!r}")
    manifest = _read_json(path)
    validate_manifest(manifest, compile_dir)
    return manifest


def load_compiler_bundle(compile_dir: str) -> dict[str, Any]:
    """Load full compile artifacts (compiler/debug only — not for runtime)."""
    manifest = load_manifest(compile_dir)
    bundle: dict[str, Any] = {
        "schema_version": "compile-artifacts-v1",
        "job_id": manifest["job_id"],
        "compile_hash": manifest["compile_hash"],
        "manifest": manifest,
    }
    mapping = {
        "product_spec": FILE_PRODUCT,
        "creative_spec": FILE_CREATIVE,
        "edit_specs": FILE_EDITS,
        "execution_request": FILE_EXECUTION,
        "provider_prompt": FILE_PROMPT,
        "execution_plan": FILE_PLAN,
        "graph": FILE_GRAPH,
    }
    for key, filename in mapping.items():
        bundle[key] = _read_json(os.path.join(compile_dir, filename))
    runtime_ctx = _read_json(os.path.join(compile_dir, FILE_RUNTIME_CTX))
    bundle["job_dir"] = runtime_ctx.get("job_dir", ".")
    bundle["persist_context"] = runtime_ctx.get("persist_context") or {}
    return bundle


def load_runtime_bundle(compile_dir: str) -> dict[str, Any]:
    """Load runtime-legal bundle only. Rejects spec languages in output."""
    manifest = load_manifest(compile_dir)
    graph = _read_json(os.path.join(compile_dir, FILE_GRAPH))
    execution_plan = _read_json(os.path.join(compile_dir, FILE_PLAN))
    execution_request = _read_json(os.path.join(compile_dir, FILE_EXECUTION))
    runtime_ctx = _read_json(os.path.join(compile_dir, FILE_RUNTIME_CTX))

    bundle: dict[str, Any] = {
        "schema_version": RUNTIME_BUNDLE_SCHEMA,
        "job_id": manifest["job_id"],
        "compile_hash": manifest["compile_hash"],
        "manifest_path": os.path.abspath(compile_dir),
        "graph": graph,
        "execution_plan": execution_plan,
        "execution_request": execution_request,
        "job_dir": runtime_ctx.get("job_dir", "."),
        "persist_context": runtime_ctx.get("persist_context") or {},
        "dry_run": manifest.get("dry_run", True),
    }
    for key in _FORBIDDEN_RUNTIME_KEYS:
        if key in bundle:
            raise CompileBundleError(f"Runtime bundle must not contain {key!r}.")
    return bundle


def runtime_artifacts_view(runtime_bundle: dict[str, Any]) -> dict[str, Any]:
    """Shape expected by graph_runtime (execution-only fields)."""
    if runtime_bundle.get("schema_version") != RUNTIME_BUNDLE_SCHEMA:
        raise CompileBundleError("Expected runtime-bundle-v1.")
    for key in _FORBIDDEN_RUNTIME_KEYS:
        if key in runtime_bundle:
            raise CompileBundleError(f"Illegal runtime field: {key!r}")
    return {
        "schema_version": "runtime-artifacts-v1",
        "job_id": runtime_bundle["job_id"],
        "job_dir": runtime_bundle["job_dir"],
        "compile_hash": runtime_bundle["compile_hash"],
        "execution_request": runtime_bundle["execution_request"],
        "execution_plan": runtime_bundle["execution_plan"],
        "persist_context": runtime_bundle["persist_context"],
    }


def default_compile_dir(job_dir: str, job_id: str) -> str:
    return os.path.join(job_dir, "compile", job_id)
