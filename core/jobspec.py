#!/usr/bin/env python3
"""JobSpec: load + validate the universal contract (dep-free).

We deliberately hand-validate the fields we depend on rather than pull a
jsonschema dependency, so the core runs on a bare python3. job.schema.json
remains the published contract (used by editors / a future API layer).
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "job.schema.json")
SCHEMA_VERSION = "job-v1"


class JobSpecError(ValueError):
    """Raised when a JobSpec is malformed. Message is user-facing."""


def load(path):
    """Read a JobSpec JSON file, validate it, and return the dict.

    The job's own directory is stored under _dir so relative paths
    (spec.cad, reference, output.dir) resolve against the job file."""
    with open(path) as f:
        job = json.load(f)
    job["_dir"] = os.path.dirname(os.path.abspath(path))
    validate(job)
    return job


def validate(job):
    """Assert the fields the runner depends on. Raise JobSpecError on any gap."""
    if not isinstance(job, dict):
        raise JobSpecError("JobSpec must be a JSON object.")

    ver = job.get("schema_version")
    if ver != SCHEMA_VERSION:
        raise JobSpecError(
            f"schema_version must be '{SCHEMA_VERSION}', got {ver!r}."
        )

    _id = job.get("id", "")
    if not _id or not all(c.islower() or c.isdigit() or c == "-" for c in _id):
        raise JobSpecError(
            f"id must be a lowercase slug [a-z0-9-], got {_id!r}."
        )

    spec = job.get("spec")
    if not isinstance(spec, dict) or not spec.get("prompt_template"):
        raise JobSpecError("spec.prompt_template is required.")

    engine = job.get("engine")
    if not isinstance(engine, dict) or not engine.get("name") or not engine.get("model"):
        raise JobSpecError("engine.name and engine.model are required.")

    out = job.get("output") or {}
    if not isinstance(out, dict):
        raise JobSpecError("output must be an object.")
    count = out.get("count", 1)
    if not isinstance(count, int) or not (1 <= count <= 4):
        raise JobSpecError(f"output.count must be an integer 1..4, got {count!r}.")

    ref = job.get("reference")
    if ref is not None and not isinstance(ref, (str, dict, list)):
        raise JobSpecError(
            "reference must be null, a string, an object with 'image', "
            "or a list of strings/objects."
        )
    if isinstance(ref, dict) and not ref.get("image"):
        raise JobSpecError("reference object requires an 'image' field.")
    if isinstance(ref, list):
        for i, item in enumerate(ref):
            if not isinstance(item, (str, dict)):
                raise JobSpecError(
                    f"reference[{i}] must be a string or an object with 'image'."
                )
            if isinstance(item, dict) and not item.get("image"):
                raise JobSpecError(f"reference[{i}] object requires an 'image' field.")

    return True


def output(job):
    """Return the output block with defaults applied."""
    out = dict(job.get("output") or {})
    out.setdefault("imgtype", "hero")
    out.setdefault("count", 1)
    return out
