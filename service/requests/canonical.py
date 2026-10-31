"""RFC 8785 (JCS) canonical JSON and the contract digests of contract.md §3.

* ``canonical_bytes`` — RFC 8785: UTF-16 code-unit key order, no whitespace,
  ECMAScript number serialisation, minimal string escaping. Non-finite numbers,
  integers outside the JavaScript exact range, lone surrogates and non-JSON
  types are refused (never coerced).
* ``artifact_sha256`` — saved document with only its *top-level* ``sha256``
  omitted (nested receipt digests stay in the preimage).
* ``semantic_sha256`` — fixed v1 projections (request, treatment) that exclude
  audit/admission noise so the same creative input hashes the same across runs.
* ``loads_strict`` — JSON parsing that refuses duplicate keys and non-finite
  constants, as the contract requires.
"""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from typing import Any

MAX_SAFE_INTEGER = 9007199254740991


class CanonicalError(ValueError):
    """Input is not representable in RFC 8785 canonical JSON."""


def _number(value: float | int) -> str:
    if isinstance(value, bool):  # pragma: no cover - handled by caller
        raise CanonicalError("boolean is not a number")
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalError("integer outside the exact JavaScript range")
        return str(value)
    if not math.isfinite(value):
        raise CanonicalError("non-finite number")
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
            raise CanonicalError("lone surrogate in string")
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
                raise CanonicalError("object key is not a string")
        out.append("{")
        for i, key in enumerate(sorted(value, key=_utf16_key)):
            if i:
                out.append(",")
            out.append(_string(key))
            out.append(":")
            _serialise(value[key], out)
        out.append("}")
    else:
        raise CanonicalError(f"type {type(value).__name__} is not JSON")


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


def artifact_preimage(document: dict) -> dict:
    """Saved document minus only its own top-level sha256 (contract.md §3)."""
    return {k: v for k, v in document.items() if k != "sha256"}


def artifact_sha256(document: dict) -> str:
    return jcs_sha256(artifact_preimage(document))


def text_sha256(text: str) -> str:
    """Exact UTF-8 text, no trimming or normalisation."""
    return sha256_hex(text.encode("utf-8"))


# -- semantic projections (contract.md §3, fixed for v1) -----------------------
def _by_id(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda item: item.get("id", ""))


def request_projection(request: dict) -> dict:
    origin = dict(request["origin"])
    origin.pop("generation_eligibility", None)   # admission observation, not creative content
    return {
        "schema_version": request["schema_version"],
        "origin": origin,
        "brand_package": request["brand_package"],
        "offering": request["offering"],
        "audiences": request["audiences"],
        "objective": request["objective"],
        "hook": request["hook"],
        "locale": request["locale"],
        "exact_copy": _by_id(request["exact_copy"]),
        "formats": _by_id(request["formats"]),
        "references": _by_id(request["references"]),
        "provider_selection": request["provider_selection"],
        "constraints": request["constraints"],
    }


def treatment_projection(treatment: dict) -> dict:
    return {
        "schema_version": treatment["schema_version"],
        "direction": treatment["direction"],
        # shots have no sequence_start_frame in treatment-v1; their array order is the sequence
        "shots": treatment["shots"],
        "sources": treatment["sources"],
    }


def request_semantic_sha256(request: dict) -> str:
    return jcs_sha256(request_projection(request))


def treatment_semantic_sha256(treatment: dict) -> str:
    return jcs_sha256(treatment_projection(treatment))


# -- strict parsing ------------------------------------------------------------
def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise CanonicalError(f"duplicate JSON key {key!r}")
        out[key] = value
    return out


def _no_constant(name):
    raise CanonicalError(f"non-finite JSON constant {name}")


def loads_strict(raw: str | bytes) -> Any:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
