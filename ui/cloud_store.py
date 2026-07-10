#!/usr/bin/env python3
"""Supabase-backed data store for the operator UI — the cloud replacement for
tenant disk reads/writes (brief #30201).

Dep-free by design (mirrors core/tracking.py): talks to Supabase over its REST
surfaces with urllib only, so a fresh checkout with just SUPABASE_URL +
SUPABASE_SERVICE_ROLE_KEY can serve the full UI with no disk access and no
extra pip installs.

Two REST surfaces are used:
  * PostgREST  (<url>/rest/v1/<table>)              — rows
  * Storage    (<url>/storage/v1/object/sign/...)   — signed media URLs

`enabled()` gates every cloud path in server.py; when it returns False the
server falls back to its legacy disk behaviour, so this module is additive and
safe to ship before the buckets/tables are provisioned.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

REFERENCE_BUCKET = "reference-images"
OUTPUT_BUCKET = "rendered-outputs"
_SIGNED_URL_TTL = int(os.environ.get("IMAGE_PIPELINE_SIGNED_URL_TTL", "3600"))


def _url() -> str:
    return (os.environ.get("SUPABASE_URL") or "").rstrip("/")


def _key() -> str:
    # service role preferred; accept a couple of common aliases
    return (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_KEY")
        or ""
    )


def enabled() -> bool:
    """True when the UI should read/write Postgres+Storage instead of disk."""
    return bool(_url() and _key())


class CloudStoreError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# low-level REST
# ---------------------------------------------------------------------------
def _headers(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    key = _key()
    h = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _request(method: str, path: str, *, params: Optional[dict] = None,
             body: Any = None, headers: Optional[dict] = None) -> Any:
    if not enabled():
        raise CloudStoreError("cloud store not configured (SUPABASE_URL/key missing)")
    url = f"{_url()}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=_headers(headers))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if not raw:
                return None
            ctype = resp.headers.get("Content-Type", "")
            if "application/json" in ctype:
                return json.loads(raw)
            return raw
    except urllib.error.HTTPError as e:  # surface PostgREST error body
        detail = e.read().decode("utf-8", "replace")
        raise CloudStoreError(f"{method} {path} -> {e.code}: {detail}") from e


# ---------------------------------------------------------------------------
# PostgREST table helpers
# ---------------------------------------------------------------------------
def select(table: str, *, filters: Optional[dict] = None,
           order: Optional[str] = None, limit: Optional[int] = None,
           select_cols: str = "*") -> list[dict]:
    params: dict[str, Any] = {"select": select_cols}
    if filters:
        params.update(filters)
    if order:
        params["order"] = order
    if limit:
        params["limit"] = limit
    return _request("GET", f"/rest/v1/{table}", params=params) or []


def insert(table: str, row: dict, *, upsert: bool = False,
           on_conflict: Optional[str] = None) -> dict:
    prefer = "return=representation"
    if upsert:
        prefer += ",resolution=merge-duplicates"
    params = {"on_conflict": on_conflict} if on_conflict else None
    out = _request("POST", f"/rest/v1/{table}", params=params, body=[row],
                   headers={"Prefer": prefer})
    return out[0] if isinstance(out, list) and out else (out or {})


def update(table: str, filters: dict, patch: dict) -> list[dict]:
    return _request("PATCH", f"/rest/v1/{table}", params=filters, body=patch,
                    headers={"Prefer": "return=representation"}) or []


def delete(table: str, filters: dict) -> None:
    _request("DELETE", f"/rest/v1/{table}", params=filters)


# ---------------------------------------------------------------------------
# Storage — signed URLs (replaces disk FileResponse in /media)
# ---------------------------------------------------------------------------
def signed_url(bucket: str, object_path: str,
               ttl: int = _SIGNED_URL_TTL) -> str:
    """Mint a short-lived signed URL for a private Storage object."""
    object_path = object_path.lstrip("/")
    resp = _request(
        "POST",
        f"/storage/v1/object/sign/{bucket}/{urllib.parse.quote(object_path)}",
        body={"expiresIn": ttl},
    )
    signed = (resp or {}).get("signedURL") or (resp or {}).get("signedUrl")
    if not signed:
        raise CloudStoreError(f"no signedURL for {bucket}/{object_path}: {resp}")
    if signed.startswith("/"):
        signed = f"{_url()}/storage/v1{signed}" if not signed.startswith(
            "/storage") else f"{_url()}{signed}"
    return signed


def upload_object(bucket: str, object_path: str, data: bytes,
                  content_type: str = "application/octet-stream") -> str:
    """Upload bytes to Storage (used by the importer). Returns the object key."""
    object_path = object_path.lstrip("/")
    url = (f"{_url()}/storage/v1/object/{bucket}/"
           f"{urllib.parse.quote(object_path)}")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "apikey": _key(),
            "Authorization": f"Bearer {_key()}",
            "Content-Type": content_type,
            "x-upsert": "true",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120):
            return object_path
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise CloudStoreError(f"upload {bucket}/{object_path} -> "
                              f"{e.code}: {detail}") from e


# ---------------------------------------------------------------------------
# Domain helpers — the surface server.py calls
# ---------------------------------------------------------------------------
def workspace_id_for_slug(slug: str) -> Optional[str]:
    rows = select("workspaces", filters={"slug": f"eq.{slug}"}, limit=1,
                  select_cols="id")
    return rows[0]["id"] if rows else None


def list_products(workspace_slug: str) -> list[dict]:
    wid = workspace_id_for_slug(workspace_slug)
    if not wid:
        return []
    return select("products", filters={"workspace_id": f"eq.{wid}"},
                  order="name.asc")


def get_product(workspace_slug: str, slug: str) -> Optional[dict]:
    wid = workspace_id_for_slug(workspace_slug)
    if not wid:
        return None
    rows = select("products",
                  filters={"workspace_id": f"eq.{wid}", "slug": f"eq.{slug}"},
                  limit=1)
    return rows[0] if rows else None


def upsert_product(workspace_slug: str, slug: str, name: str,
                   spec: Optional[dict] = None,
                   cad_md: Optional[str] = None) -> dict:
    wid = workspace_id_for_slug(workspace_slug)
    if not wid:
        raise CloudStoreError(f"unknown workspace: {workspace_slug}")
    row = {"workspace_id": wid, "slug": slug, "name": name}
    if spec is not None:
        row["spec"] = spec
    if cad_md is not None:
        row["cad_md"] = cad_md
    return insert("products", row, upsert=True,
                  on_conflict="workspace_id,slug")


def set_canonical_image(product_id: str, artifact_id: str) -> None:
    update("products", {"id": f"eq.{product_id}"},
           {"canonical_image_artifact_id": artifact_id})


def _sign_artifact(row: dict) -> dict:
    """Attach a fresh signed URL so the client never sees a disk path."""
    try:
        row = dict(row)
        row["url"] = signed_url(row.get("bucket") or OUTPUT_BUCKET,
                                row["storage_path"])
        row["thumb_url"] = row["url"]
    except Exception as e:  # keep the row usable even if signing fails
        row = dict(row)
        row["url"] = None
        row["sign_error"] = str(e)
    return row


def list_artifacts(workspace_slug: str, *, product_slug: Optional[str] = None,
                   status: Optional[str] = None, limit: int = 200,
                   sign: bool = True) -> list[dict]:
    wid = workspace_id_for_slug(workspace_slug)
    if not wid:
        return []
    filters: dict[str, Any] = {"workspace_id": f"eq.{wid}"}
    if status and status != "all":
        filters["status"] = f"eq.{status}"
    if product_slug:
        prod = get_product(workspace_slug, product_slug)
        if not prod:
            return []
        filters["product_id"] = f"eq.{prod['id']}"
    rows = select("artifacts", filters=filters, order="created_at.desc",
                  limit=limit)
    return [_sign_artifact(r) for r in rows] if sign else rows


def get_artifact(artifact_id: str, *, sign: bool = True) -> Optional[dict]:
    rows = select("artifacts", filters={"id": f"eq.{artifact_id}"}, limit=1)
    if not rows:
        return None
    return _sign_artifact(rows[0]) if sign else rows[0]


def set_artifact_status(artifact_id: str, status: str,
                        comment: Optional[str] = None) -> Optional[dict]:
    patch: dict[str, Any] = {"status": status}
    if comment is not None:
        patch["comment"] = comment
    out = update("artifacts", {"id": f"eq.{artifact_id}"}, patch)
    return out[0] if out else None


def set_artifact_comment(artifact_id: str, comment: str) -> Optional[dict]:
    out = update("artifacts", {"id": f"eq.{artifact_id}"}, {"comment": comment})
    return out[0] if out else None


def add_comment(artifact_id: str, body: str, *, region: Optional[dict] = None,
                parent_comment_id: Optional[str] = None,
                author: Optional[str] = None) -> dict:
    return insert("artifact_comments", {
        "artifact_id": artifact_id,
        "body": body,
        "region": region,
        "parent_comment_id": parent_comment_id,
        "author": author,
    })


def list_comments(artifact_id: str) -> list[dict]:
    return select("artifact_comments",
                  filters={"artifact_id": f"eq.{artifact_id}"},
                  order="created_at.asc")


def upsert_artifact(workspace_id: str, storage_path: str, file_name: str,
                    *, bucket: str = OUTPUT_BUCKET, **cols: Any) -> dict:
    row = {
        "workspace_id": workspace_id,
        "bucket": bucket,
        "storage_path": storage_path,
        "file_name": file_name,
    }
    row.update({k: v for k, v in cols.items() if v is not None})
    return insert("artifacts", row, upsert=True,
                  on_conflict="workspace_id,bucket,storage_path")


def create_run(workspace_id: str, **cols: Any) -> dict:
    row = {"workspace_id": workspace_id}
    row.update({k: v for k, v in cols.items() if v is not None})
    return insert("runs", row)


def list_runs(workspace_slug: str, *, limit: int = 100) -> list[dict]:
    wid = workspace_id_for_slug(workspace_slug)
    if not wid:
        return []
    return select("runs", filters={"workspace_id": f"eq.{wid}"},
                  order="created_at.desc", limit=limit)


def upsert_shot(workspace_id: str, slug: str, job_spec: dict,
                *, product_id: Optional[str] = None,
                prompt_template: Optional[str] = None,
                status: str = "draft") -> dict:
    row = {
        "workspace_id": workspace_id,
        "slug": slug,
        "job_spec": job_spec,
        "status": status,
    }
    if product_id:
        row["product_id"] = product_id
    if prompt_template is not None:
        row["prompt_template"] = prompt_template
    return insert("shots", row, upsert=True, on_conflict="workspace_id,slug")
