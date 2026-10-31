"""RFC 8785 (JCS) canonical JSON: the single implementation for every Creative Studio module.

contract.md §3: artifact, semantic, compile, node-input and dispatch digests are SHA-256 of
the RFC 8785 canonical UTF-8 form. UTF-16 code-unit key order, no whitespace, ECMAScript
number serialisation, minimal string escaping. Non-finite numbers, integers outside the
JavaScript exact range, lone surrogates and non-JSON types are refused, never coerced.

CS-INT: CS-B2, CS-B3 and CS-B4 each shipped a copy. CS-B3's mis-formatted numbers in
[1e-6, 1e-4) as exponents ("1e-5" instead of ECMAScript "0.00001"), so the same document
could hash differently per module. All three now delegate here; tests/creative_studio
test_cs_int_unit + test_cs_int_real_db hold this module, the SQL canonicaliser and an
independent reference to one output.
"""
from __future__ import annotations

import hashlib
import math
from decimal import Decimal
from typing import Any

MAX_SAFE_INTEGER = 9007199254740991


class JcsError(ValueError):
    """Input is not representable in RFC 8785 canonical JSON."""


def _number(value: float | int) -> str:
    if isinstance(value, bool):  # pragma: no cover - handled by caller
        raise JcsError("boolean is not a number")
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise JcsError("integer outside the exact JavaScript range")
        return str(value)
    if not math.isfinite(value):
        raise JcsError("non-finite number")
    if value == 0:
        return "0"   # covers -0.0
    sign = "-" if value < 0 else ""
    # repr() is the shortest round-trip decimal, the same digit choice ES requires.
    digits_t, exp = Decimal(repr(abs(value))).as_tuple()[1:]
    digits = "".join(map(str, digits_t)).lstrip("0") or "0"
    # strip trailing zeros into the exponent
    stripped = digits.rstrip("0")
    exp += len(digits) - len(stripped)
    digits = stripped
    k = len(digits)
    n = exp + k
    if k <= n <= 21:
        out = digits + "0" * (n - k)
    elif 0 < n <= 21:
        out = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        mant = digits[0] + ("." + digits[1:] if k > 1 else "")
        out = f"{mant}e{'+' if e >= 0 else '-'}{abs(e)}"
    return sign + out


_ESC = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _string(value: str) -> str:
    out = ['"']
    for ch in value:
        cp = ord(ch)
        if 0xD800 <= cp <= 0xDFFF:
            raise JcsError("lone surrogate in string")
        if ch in _ESC:
            out.append(_ESC[ch])
        elif cp < 0x20:
            out.append(f"\\u{cp:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _utf16_key(key: str) -> list[int]:
    return list(key.encode("utf-16-be"))


def _serialise(value: Any, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, (int, float)):
        out.append(_number(value))
    elif isinstance(value, str):
        out.append(_string(value))
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _serialise(item, out)
        out.append("]")
    elif isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise JcsError("object key is not a string")
        out.append("{")
        for i, key in enumerate(sorted(value, key=_utf16_key)):
            if i:
                out.append(",")
            out.append(_string(key))
            out.append(":")
            _serialise(value[key], out)
        out.append("}")
    else:
        raise JcsError(f"type {type(value).__name__} is not JSON")


def canonical_text(value: Any) -> str:
    out: list[str] = []
    _serialise(value, out)
    return "".join(out)


def canonical_bytes(value: Any) -> bytes:
    return canonical_text(value).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jcs_sha256(value: Any) -> str:
    return sha256_hex(canonical_bytes(value))
