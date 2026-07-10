#!/usr/bin/env python3
"""REFERENCE adapter: local — resolve the look-anchor to a local file path.

Accepts a local path (relative to the job dir) or an http(s) URL (downloaded to
a temp file). Returns None when no reference is set.

SaaS swap: an s3/drive adapter resolves the same reference field to a signed
local temp; the engine adapter still just receives a path.
"""
from __future__ import annotations

import os
import tempfile
import urllib.request


def resolve(reference, job_dir="."):
    """reference: None | str(path|url) | {'image': ..., 'optional': bool}
    | list of str/dict items.

    Returns a local path, a list of local paths, or None.
    List form supports multiple look-anchor images (e.g. pose + product + mark),
    each fanned out as a separate --image flag by the engine adapter."""
    if reference is None:
        return None

    if isinstance(reference, list):
        resolved = [_resolve_one(item, job_dir) for item in reference]
        paths = [p for p in resolved if p]
        return paths or None

    return _resolve_one(reference, job_dir)


def _resolve_one(reference, job_dir="."):
    """Resolve a single reference item to a local path or None."""
    optional = False
    if isinstance(reference, dict):
        image = reference.get("image")
        optional = bool(reference.get("optional", False))
    else:
        image = reference

    if not image:
        return None

    if image.startswith(("http://", "https://")):
        # Strip query/fragment before extracting ext so URLs like
        # "…/foo.jpg?v=12345" don't produce an invalid temp suffix.
        path_part = image.split("?", 1)[0].split("#", 1)[0]
        suffix = os.path.splitext(path_part)[1] or ".png"
        fd, dest = tempfile.mkstemp(suffix=suffix, prefix="ip-ref-")
        os.close(fd)
        urllib.request.urlretrieve(image, dest)
        return dest

    path = image if os.path.isabs(image) else os.path.join(job_dir, image)
    if not os.path.exists(path):
        if optional:
            print(f"  [reference] optional reference missing, skipping: {path}")
            return None
        raise FileNotFoundError(f"reference image not found: {path}")
    return path
