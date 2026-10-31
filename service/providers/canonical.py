"""Canonical JSON (RFC 8785 / JCS subset) and digests used by CS-B3.

contract.md §3: compile_hash, dispatch and artifact digests are SHA-256 of the
RFC 8785 canonical UTF-8 form. Supported values: objects with string keys,
arrays, strings, booleans, null, integers within JavaScript's exact range, and
finite floats (ECMAScript shortest round-trip form). Anything else is refused,
never coerced.
"""
from __future__ import annotations

import hashlib
import json
import math

MAX_SAFE_INTEGER = 9007199254740991


class CanonicalError(ValueError):
    pass


def _number(value) -> str:
    if isinstance(value, bool):
        raise CanonicalError("bool is not a number")
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalError("integer outside the exact JavaScript range")
        return str(value)
    if not math.isfinite(value):
        raise CanonicalError("non-finite number")
    if value == 0:
        return "0"
    if value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    text = repr(value)
    if "e" in text:
        mantissa, exponent = text.split("e")
        exp = int(exponent)
        text = f"{mantissa}e{'+' if exp > 0 else '-'}{abs(exp)}"
    return text


def _encode(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_encode(v) for v in value) + "]"
    if isinstance(value, dict):
        for k in value:
            if not isinstance(k, str):
                raise CanonicalError("object keys must be strings")
        # RFC 8785: sort by UTF-16 code units
        keys = sorted(value, key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + _encode(value[k]) for k in keys) + "}"
    raise CanonicalError(f"unsupported type {type(value).__name__}")


def canonical(value) -> bytes:
    return _encode(value).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(value) -> str:
    return sha256_hex(canonical(value))


def artifact_digest(document: dict) -> str:
    """Saved-artifact sha256: canonical document with only its top-level sha256 omitted."""
    return digest({k: v for k, v in document.items() if k != "sha256"})
