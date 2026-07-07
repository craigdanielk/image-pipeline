#!/usr/bin/env python3
"""Tenant registry — maps a tenant slug to its consumer jobs_root.

Config lives at <root>/config/tenants.json so it's inspectable/editable, not
re-derived every request. First read auto-seeds by scanning a local tenants
base dir (default ~/Developer/GitHub/tenants) for consumer workspaces —
either <slug>/image-pipeline/jobs or <slug>/tools/image-pipeline/jobs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_DEFAULT_TENANTS_BASE = Path.home() / "Developer" / "GitHub" / "tenants"


def _config_path(root: Path) -> Path:
    return root / "config" / "tenants.json"


def _discover(base_dir: Path) -> list[dict[str, str]]:
    if not base_dir.is_dir():
        return []
    found: list[dict[str, str]] = []
    for child in sorted(base_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        for candidate in (child / "image-pipeline" / "jobs", child / "tools" / "image-pipeline" / "jobs"):
            if candidate.is_dir():
                found.append({"slug": child.name, "label": child.name, "jobs_root": str(candidate)})
                break
    return found


def load_tenants(root: Path) -> list[dict[str, str]]:
    path = _config_path(root)
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("tenants", [])

    base_dir = Path(os.environ.get("IMAGE_PIPELINE_TENANTS_BASE", str(_DEFAULT_TENANTS_BASE)))
    seeded = _discover(base_dir)
    save_tenants(root, seeded)
    return seeded


def save_tenants(root: Path, tenants: list[dict[str, str]]) -> None:
    path = _config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tenants": tenants}, indent=2) + "\n", encoding="utf-8")


def get_tenant(root: Path, slug: str) -> dict[str, str] | None:
    for t in load_tenants(root):
        if t["slug"] == slug:
            return t
    return None


def add_tenant(root: Path, slug: str, jobs_root: str, label: str | None = None) -> dict[str, str]:
    tenants = load_tenants(root)
    entry = {"slug": slug, "label": label or slug, "jobs_root": jobs_root}
    tenants = [t for t in tenants if t["slug"] != slug] + [entry]
    save_tenants(root, tenants)
    return entry


def resolve_jobs_root(root: Path, tenant_slug: str | None, fallback: Path) -> Path:
    """Tenant jobs_root if known, else the caller's existing fallback (env var / repo default)."""
    if not tenant_slug:
        return fallback
    tenant = get_tenant(root, tenant_slug)
    if tenant and Path(tenant["jobs_root"]).is_dir():
        return Path(tenant["jobs_root"])
    return fallback
