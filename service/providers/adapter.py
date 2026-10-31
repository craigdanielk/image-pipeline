"""Load the single Higgsfield adapter module by file.

core/adapters/__init__.py imports the local operator pipeline (intake, jobspec,
local output); the hosted runtime must not execute that. The adapter file
itself imports only the standard library, so it is loaded directly and
registered under its canonical module name (one module object for everyone).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ADAPTER_PATH = Path(__file__).resolve().parents[2] / "core" / "adapters" / "engine_higgsfield.py"
MODULE_NAME = "core.adapters.engine_higgsfield"


def load():
    mod = sys.modules.get(MODULE_NAME)
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location(MODULE_NAME, ADAPTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(MODULE_NAME, None)
        raise
    return mod


engine_higgsfield = load()
