"""Canonical JSON (RFC 8785 / JCS subset) and digests used by CS-B3.

contract.md §3: compile_hash, dispatch and artifact digests are SHA-256 of the
RFC 8785 canonical UTF-8 form. Supported values: objects with string keys,
arrays, strings, booleans, null, integers within JavaScript's exact range, and
finite floats (ECMAScript shortest round-trip form). Anything else is refused,
never coerced.
"""
from __future__ import annotations

import hashlib

from .. import jcs as _jcs

MAX_SAFE_INTEGER = _jcs.MAX_SAFE_INTEGER
# One RFC 8785 implementation for every module (service/jcs.py, CS-INT). CS-B3's own
# number formatter wrote 1e-6 <= |x| < 1e-4 as exponents, which is not ECMAScript output.
CanonicalError = _jcs.JcsError
canonical = _jcs.canonical_bytes


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(value) -> str:
    return sha256_hex(canonical(value))


def artifact_digest(document: dict) -> str:
    """Saved-artifact sha256: canonical document with only its top-level sha256 omitted."""
    return digest({k: v for k, v in document.items() if k != "sha256"})
