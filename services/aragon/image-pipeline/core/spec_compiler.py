#!/usr/bin/env python3
"""Spec compiler — lower job-v1 JobSpecs to ExecutionRequest v1."""
from __future__ import annotations

import copy
import os
from typing import Any

import jobspec
import spec_validator


def compile_execution_request(job: dict[str, Any], *, source_path: str | None = None) -> dict[str, Any]:
    """Map a validated job-v1 dict to ExecutionRequest v1 without mutation."""
    jobspec.validate(job)
    out = jobspec.output(job)

    req: dict[str, Any] = {
        "schema_version": spec_validator.EXECUTION_SCHEMA,
        "id": job["id"],
        "subject": copy.deepcopy(job.get("subject") or {}),
        "reference": copy.deepcopy(job.get("reference")),
        "output": copy.deepcopy(out),
        "engine": copy.deepcopy(job["engine"]),
        "dry_run": bool(job.get("dry_run", False)),
        "parent_run_id": job.get("parent_run_id"),
        "provenance": {
            "compiled_from": "job-v1",
            "source_job_path": source_path or job.get("_source_path") or job.get("_dir"),
        },
    }
    spec_validator.validate_execution_request(req)
    return req


def derive_creative_from_job(
    job: dict[str, Any],
    *,
    product_spec_id: str,
    creative_id: str | None = None,
) -> dict[str, Any]:
    """Build a CreativeSpec from job-v1 campaign fields (prompt_template, defaults)."""
    jobspec.validate(job)
    out = jobspec.output(job)
    spec = job.get("spec") or {}

    creative: dict[str, Any] = {
        "schema_version": spec_validator.CREATIVE_SCHEMA,
        "id": creative_id or f"{job['id']}-creative",
        "product_spec_id": product_spec_id,
        "campaign_id": job.get("_dir", "").split(os.sep)[-1] if job.get("_dir") else job["id"],
        "prompt_template": spec["prompt_template"],
        "negative_template": spec.get("negative_template") or spec.get("negative"),
        "default_subject": copy.deepcopy(job.get("subject") or {}),
        "default_composition": out.get("composition", ""),
        "default_background": out.get("background", ""),
    }
    spec_validator.validate_creative_spec(creative)
    return creative
