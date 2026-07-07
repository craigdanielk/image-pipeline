#!/usr/bin/env python3
"""OUTPUT adapter: local — download engine results to a versioned local file.

  <out_dir>/<id>__<imgtype>__v<N>.png   (N auto-increments, never overwrites)

SaaS swap: an s3/webhook adapter uploads the same result and returns a signed
URL + fires a callback; the core just hands it the result list.
"""
from __future__ import annotations

import glob
import os
import urllib.request


def _next_version(out_dir, base):
    existing = glob.glob(os.path.join(out_dir, f"{base}__v*.png"))
    vs = []
    for p in existing:
        tail = p.rsplit("__v", 1)[-1]
        num = tail.split(".")[0]
        if num.isdigit():
            vs.append(int(num))
    return max(vs, default=0) + 1


def persist(results, job, out_dir):
    """results: [{result_url, id}]. Returns list of saved local paths."""
    out = jobout = job.get("output") or {}
    imgtype = out.get("imgtype", "hero")
    os.makedirs(out_dir, exist_ok=True)
    base = f"{job['id']}__{imgtype}"
    saved = []
    for r in results:
        v = _next_version(out_dir, base)
        dest = os.path.join(out_dir, f"{base}__v{v}.png")
        with urllib.request.urlopen(r["result_url"]) as resp, open(dest, "wb") as f:
            f.write(resp.read())
        print(f"  saved -> {dest}  (job {r.get('id', '?')})")
        saved.append(dest)
    return saved
