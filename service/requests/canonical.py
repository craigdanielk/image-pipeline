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
from typing import Any

from .. import jcs as _jcs

MAX_SAFE_INTEGER = _jcs.MAX_SAFE_INTEGER
# One RFC 8785 implementation for every module (service/jcs.py, CS-INT).
CanonicalError = _jcs.JcsError
canonical_text = _jcs.canonical_text
canonical_bytes = _jcs.canonical_bytes


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
