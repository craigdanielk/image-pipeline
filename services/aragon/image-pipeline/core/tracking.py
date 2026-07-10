#!/usr/bin/env python3
"""TRACKING — optional DB persistence layer, off by default.

Not a fifth adapter seam (intake/reference/engine/output stay the four seams
per the architecture) — this is a cross-cutting observer that mirrors run_job's
progress into Supabase (db/SCHEMA.md), called at each of the 6 pipeline stages
plus a review hook. Zero pip dependencies: raw urllib against PostgREST, same
pattern output_local.py already uses for downloads, so the "bare python3" core
promise holds even with tracking on.

Enable with --track (or IMAGE_PIPELINE_TRACK=1) and a .env with SUPABASE_URL +
SUPABASE_SERVICE_ROLE_KEY. Off (the default): every function is a no-op that
returns None instantly — zero behavior change for anyone not opted in.

HARD RULE: a tracking failure must NEVER lose or crash a real generation.
Every write is best-effort — caught, logged, swallowed. The one exception is
the pre-flight secret-lint on intake, which fails loud on purpose (better to
block before a credential lands in the DB than track-and-hope).
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

_ENABLED = None  # tri-state cache: None = not checked yet


def _load_env(job_dir):
    """Minimal .env loader — no python-dotenv dependency. Looks in job_dir,
    then walks up to the image-pipeline repo root (where the real .env lives)."""
    here = os.path.abspath(job_dir)
    for _ in range(6):
        candidate = os.path.join(here, ".env")
        if os.path.exists(candidate):
            with open(candidate) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
            return
        here = os.path.dirname(here)


def enabled(job_dir="."):
    global _ENABLED
    if _ENABLED is not None:
        return _ENABLED
    if os.environ.get("IMAGE_PIPELINE_TRACK") != "1":
        _ENABLED = False
        return False
    _load_env(job_dir)
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        print("  [tracking] IMAGE_PIPELINE_TRACK=1 but SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY "
              "not set — tracking disabled for this run.")
        _ENABLED = False
        return False
    _ENABLED = True
    return True


def _rest(method, path, body=None, params=""):
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + path + params
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def _safe(fn, *a, **kw):
    """Best-effort wrapper — tracking must never take down a real run."""
    try:
        return fn(*a, **kw)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as e:
        print(f"  [tracking] write failed (non-fatal, generation continues): {e}")
        return None


# --- secret lint (fails LOUD, on purpose — see module docstring) -----------

_SECRET_PATTERNS = [
    re.compile(r"sb_(secret|publishable)_[A-Za-z0-9_-]{10,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT-shaped
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS-shaped
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI-shaped
]


def lint_for_secrets(job):
    """Raise ValueError if anything in the JobSpec looks like a credential.
    Called before the snapshot is ever written to the DB. Not best-effort —
    this one blocks, because a leaked key in a stored jsonb blob is worse
    than a failed CLI run."""
    blob = json.dumps(job)
    for pat in _SECRET_PATTERNS:
        m = pat.search(blob)
        if m:
            raise ValueError(
                f"JobSpec appears to contain a credential ({m.group(0)[:12]}...) — "
                "refusing to track. Remove it from the job.json before running."
            )


# --- pipeline stage hooks ----------------------------------------------------

def job_created(job, product_slug=None):
    """intake stage. Returns the jobs.id (uuid) for use by every later hook,
    or None if tracking is off/failed."""
    if not enabled(job.get("_dir", ".")):
        return None
    lint_for_secrets(job)  # loud on purpose

    def _do():
        product_id = None
        cad_spec_id = None
        target_reference_id = None
        product_reference_ids = []

        if product_slug:
            existing = _rest("GET", "products", params=f"?slug=eq.{product_slug}&select=id")
            if existing:
                product_id = existing[0]["id"]
            else:
                created = _rest("POST", "products", {"name": product_slug, "slug": product_slug})
                product_id = created[0]["id"]

            # Auto-link the authoring layer if it's been curated for this
            # product — get-or-create above only handles `products`; cad_specs
            # and reference_images stay manually authored (author_reference_layer
            # helper / direct insert), this just wires whatever already exists
            # into the new job row so cad_spec_id/target_reference_id aren't
            # silently left null when curation HAS happened.
            cads = _rest("GET", "cad_specs",
                         params=f"?product_id=eq.{product_id}&scope=eq.whole_product"
                                f"&order=version.desc&limit=1&select=id")
            if cads:
                cad_spec_id = cads[0]["id"]

            # Multiple target_anchor rows can exist per product (e.g. 2 flagged
            # shots for one product) — "most recent" is a silent-wrong-pick trap
            # when they don't match what this JobSpec's own `reference` field
            # points to. Match on the actual reference path/URL first; only
            # fall back to most-recent when there's exactly one candidate or no
            # match at all (better than leaving it null).
            anchors = _rest("GET", "reference_images",
                            params=f"?product_id=eq.{product_id}&role=eq.target_anchor"
                                   f"&order=created_at.desc&select=id,storage_locations")
            if anchors:
                job_ref = job.get("reference")
                job_ref_path = job_ref.get("image") if isinstance(job_ref, dict) else job_ref
                matched = None
                if job_ref_path:
                    # Basename match, not full-path — job.json reference fields
                    # use ../-relative paths, authored reference_images rows use
                    # repo-relative paths; different prefixes, same file. These
                    # local output filenames are UUID-suffixed by Higgsfield, so
                    # a basename collision between unrelated images is a
                    # vanishingly small risk.
                    job_ref_base = os.path.basename(job_ref_path)
                    for a in anchors:
                        for loc in a.get("storage_locations") or []:
                            if os.path.basename(loc.get("location", "")) == job_ref_base:
                                matched = a["id"]
                                break
                        if matched:
                            break
                if matched:
                    target_reference_id = matched
                elif len(anchors) == 1:
                    target_reference_id = anchors[0]["id"]
                else:
                    print(f"  [tracking] {len(anchors)} target_anchor candidates for "
                          f"product_slug={product_slug!r}, none matched this job's "
                          f"reference={job_ref_path!r} — target_reference_id left null "
                          "rather than guessing wrong.")

            refs = _rest("GET", "reference_images",
                        params=f"?product_id=eq.{product_id}&role=eq.product_ref&select=id")
            product_reference_ids = [r["id"] for r in (refs or [])]

        spec = job.get("spec") or {}
        row = _rest("POST", "jobs", {
            "product_id": product_id,
            "cad_spec_id": cad_spec_id,
            "target_reference_id": target_reference_id,
            "product_reference_ids": product_reference_ids,
            "external_slug": job.get("id", "(unknown — malformed spec)"),
            "schema_version": job.get("schema_version", "job-v1"),
            "desired_end_state": spec.get("prompt_template", "")[:2000],
            "subject": job.get("subject") or {},
            "output_requirements": job.get("output") or {},
            "prompt_template": spec.get("prompt_template", ""),
            "negative_prompt": spec.get("negative"),
            "engine": job.get("engine") or {},
            "job_spec_snapshot": job,
            "status": "draft",
        })
        row_id = row[0]["id"]
        if not cad_spec_id or not target_reference_id:
            print(f"  [tracking] job {row_id}: no curated "
                  f"{'cad_spec' if not cad_spec_id else ''}"
                  f"{' + ' if not cad_spec_id and not target_reference_id else ''}"
                  f"{'target_reference' if not target_reference_id else ''} "
                  f"found for product_slug={product_slug!r} — field(s) left null.")
        return row_id

    return _safe(_do)


def job_intake_failed(file_path, raw_error):
    """intake stage, malformed JSON case — no job.id exists yet, so this is a
    synthetic row keyed on the file path instead."""
    if not enabled(os.path.dirname(file_path) or "."):
        return None
    return _safe(lambda: _rest("POST", "jobs", {
        "external_slug": os.path.basename(file_path),
        "desired_end_state": "(intake failed — malformed JobSpec)",
        "prompt_template": "",
        "engine": {},
        "job_spec_snapshot": {"_intake_error": str(raw_error), "_source_file": file_path},
        "status": "failed",
        "error": str(raw_error),
    })[0]["id"])


def status(job_row_id, new_status, **extra):
    if not job_row_id:
        return
    _safe(_rest, "PATCH", "jobs", dict(status=new_status, **extra), params=f"?id=eq.{job_row_id}")


def prompt_built(job_row_id, resolved_prompt):
    if not job_row_id:
        return
    _safe(_rest, "PATCH", "jobs", {"resolved_prompt": resolved_prompt},
          params=f"?id=eq.{job_row_id}")


def cost_estimated(job_row_id, cost_dict):
    if not job_row_id:
        return
    credits = None
    if isinstance(cost_dict, dict):
        credits = cost_dict.get("credits") or cost_dict.get("credits_exact")
    _safe(_rest, "PATCH", "jobs",
          {"status": "cost_estimated", "cost_credits_estimated": credits},
          params=f"?id=eq.{job_row_id}")


def generation_started(job_row_id):
    status(job_row_id, "generating")


def generated(job_row_id, results, cost_credits_actual=None):
    """Called immediately after engine.generate() returns — BEFORE local
    persist touches disk. This is the fix for the 'credits spent but download
    fails, image unrecoverably lost' gap: the remote result_url and per-image
    engine response land in the DB here, independent of whether the local
    save later succeeds. Returns {result_url: job_outputs.id} for persist()
    to update with the local path afterward."""
    if not job_row_id:
        return {}
    _safe(_rest, "PATCH", "jobs",
          {"status": "generated", "cost_credits_actual": cost_credits_actual},
          params=f"?id=eq.{job_row_id}")

    output_ids = {}

    def _do():
        for i, r in enumerate(results):
            url = r.get("result_url")
            locations = [{"kind": "engine_url", "location": url}] if url else []
            row = _rest("POST", "job_outputs", {
                "job_id": job_row_id,
                "candidate_index": i,
                "storage_locations": locations,
                "status": "generated",
                "engine_response": r,
            })
            if url:
                output_ids[url] = row[0]["id"]
        return output_ids

    return _safe(_do) or {}


def generation_failed(job_row_id, error):
    status(job_row_id, "failed", error=str(error)[:2000])


def output_persisted(job_row_id, output_ids_by_url, result_url, local_path):
    """persist stage — append the local disk path to whichever job_outputs
    row this result_url already landed in from generated(). If generated()
    never ran with tracking on (e.g. tracking was enabled mid-flight, or the
    job never went through the DB), falls back to inserting a fresh row so
    the local file is at least recorded."""
    if not job_row_id:
        return
    out_id = (output_ids_by_url or {}).get(result_url)

    def _do():
        if out_id:
            existing = _rest("GET", "job_outputs", params=f"?id=eq.{out_id}&select=storage_locations")
            locations = (existing[0]["storage_locations"] if existing else []) or []
            locations.append({"kind": "local_path", "location": local_path})
            _rest("PATCH", "job_outputs",
                  {"storage_locations": locations, "file_name": os.path.basename(local_path)},
                  params=f"?id=eq.{out_id}")
        else:
            _rest("POST", "job_outputs", {
                "job_id": job_row_id,
                "storage_locations": [{"kind": "local_path", "location": local_path}],
                "file_name": os.path.basename(local_path),
                "status": "generated",
            })

    _safe(_do)
