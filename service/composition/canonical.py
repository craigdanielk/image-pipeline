"""RFC 8785 (JCS) canonical JSON and the contract's digest rules (contract.md §3).

Rejects non-finite numbers, integers outside the JavaScript exact range,
lone surrogates and duplicate object keys (via `strict_loads`).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .. import jcs as _jcs

MAX_SAFE_INTEGER = _jcs.MAX_SAFE_INTEGER
# One RFC 8785 implementation for every module (service/jcs.py, CS-INT).
CanonicalizationError = _jcs.JcsError
canonical_bytes = _jcs.canonical_bytes


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifact_sha256(document: dict) -> str:
    """Artifact digest: JCS of the saved document with only top-level sha256 omitted."""
    return sha256_hex(canonical_bytes({k: v for k, v in document.items() if k != "sha256"}))


def text_sha256(text: str) -> str:
    """Exact UTF-8 text, no trimming or normalization."""
    return sha256_hex(text.encode("utf-8"))


def _reject_duplicates(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise CanonicalizationError(f"duplicate JSON key {key!r}")
        seen[key] = value
    return seen


def _reject_constant(name):
    raise CanonicalizationError(f"non-finite number {name}")


def strict_loads(raw: str | bytes) -> Any:
    """Parse JSON refusing duplicate keys and NaN/Infinity."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
