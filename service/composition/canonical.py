"""RFC 8785 (JCS) canonical JSON and the contract's digest rules (contract.md §3).

Rejects non-finite numbers, integers outside the JavaScript exact range,
lone surrogates and duplicate object keys (via `strict_loads`).
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

MAX_SAFE_INTEGER = 9007199254740991


class CanonicalizationError(ValueError):
    pass


def _es_number(value: float) -> str:
    """ECMAScript Number::toString for a finite double (RFC 8785 §3.2.2.3)."""
    if math.isnan(value) or math.isinf(value):
        raise CanonicalizationError("non-finite number")
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    text = repr(abs(value))  # shortest round-trip digits
    mantissa, _, exp_text = text.partition("e")
    exponent = int(exp_text) if exp_text else 0
    int_part, _, frac_part = mantissa.partition(".")
    digits = int_part + frac_part
    point = len(int_part) + exponent  # value = 0.<digits> * 10**point
    stripped = digits.lstrip("0")
    point -= len(digits) - len(stripped)
    digits = stripped.rstrip("0") or "0"
    k, n = len(digits), point
    if k <= n <= 21:
        out = digits + "0" * (n - k)
    elif 0 < n <= 21:
        out = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        e_text = ("+" if e >= 0 else "-") + str(abs(e))
        out = (digits if k == 1 else digits[0] + "." + digits[1:]) + "e" + e_text
    return sign + out


def _string(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise CanonicalizationError("invalid unicode (lone surrogate)") from None
    return json.dumps(value, ensure_ascii=False)


def _encode(value: Any, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalizationError("integer outside JavaScript exact range")
        out.append(str(value))
    elif isinstance(value, float):
        out.append(_es_number(value))
    elif isinstance(value, str):
        out.append(_string(value))
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _encode(item, out)
        out.append("]")
    elif isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise CanonicalizationError("object key is not a string")
        out.append("{")
        for i, key in enumerate(sorted(value, key=lambda k: k.encode("utf-16-be"))):
            if i:
                out.append(",")
            out.append(_string(key))
            out.append(":")
            _encode(value[key], out)
        out.append("}")
    else:
        raise CanonicalizationError(f"unsupported JSON value {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    out: list[str] = []
    _encode(value, out)
    return "".join(out).encode("utf-8")


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
