#!/usr/bin/env python3
"""Adapter registry — the four seams that touch the outside world.

The pure core (run_job) never imports a concrete adapter directly; it asks the
registry by name. Swapping Higgsfield for fal, or local disk for S3, is a
registry entry — the core does not change.

Seams:
  intake     — how a JobSpec arrives      (cli file | http body | queue)
  reference  — how the look-anchor loads  (local | url | s3 | drive)
  engine     — the generative backend     (higgsfield | fal | replicate)
  output     — where results are written  (local | s3 | webhook)
"""
from __future__ import annotations

from . import intake_cli, reference_local, engine_higgsfield, output_local

_INTAKE = {"cli": intake_cli}
_REFERENCE = {"local": reference_local}
_ENGINE = {"higgsfield": engine_higgsfield}
_OUTPUT = {"local": output_local}


def _get(table, kind, name):
    if name not in table:
        raise KeyError(
            f"No {kind} adapter '{name}'. Available: {sorted(table)}."
        )
    return table[name]


def get_intake(name="cli"):
    return _get(_INTAKE, "intake", name)


def get_reference(name="local"):
    return _get(_REFERENCE, "reference", name)


def get_engine(name):
    return _get(_ENGINE, "engine", name)


def get_output(name="local"):
    return _get(_OUTPUT, "output", name)
