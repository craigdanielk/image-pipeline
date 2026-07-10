#!/usr/bin/env python3
"""INTAKE adapter: cli — a JobSpec arrives as a file path (or '-' for stdin).

SaaS swap: an http intake reads the same JobSpec from a POST body; the core
downstream is identical.
"""
from __future__ import annotations

import json
import sys

import jobspec


def load(source):
    """source: a path to a .job.json, or '-' to read JSON from stdin."""
    if source == "-":
        job = json.load(sys.stdin)
        job["_dir"] = "."
        jobspec.validate(job)
        return job
    return jobspec.load(source)
