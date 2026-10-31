"""Validation against the bundled contract schema files (never fetched remotely).

The schemas in docs/creative-studio/contracts are the single source of truth;
this module only loads them into a local registry and reports JSON Pointers.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from .errors import CompositionError

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "docs" / "creative-studio" / "contracts"
BASE_URI = "https://schemas.creative-studio.invalid/v1/"

SCHEMA_VERSIONS = {
    "cs-composition-v1": "composition-v1.schema.json",
    "cs-export-v1": "export-v1.schema.json",   # read-only: manifests issued before the v2 amendment
    "cs-export-v2": "export-v2.schema.json",
    "cs-mos-handoff-v1": "mos-handoff-v1.schema.json",
    "cs-mos-ingestion-receipt-v1": "mos-ingestion-receipt-v1.schema.json",
    "brand-package-v1": "brand-package-v1.schema.json",
    "cs-recipe-v1": "recipe-v1.schema.json",
    "cs-recipe-command-v1": "recipe-command-v1.schema.json",
}


@lru_cache(maxsize=1)
def _registry() -> Registry:
    resources = []
    for path in sorted(CONTRACTS_DIR.glob("*.schema.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        resources.append((doc["$id"], Resource.from_contents(doc, default_specification=DRAFT202012)))
    return Registry().with_resources(resources)


@lru_cache(maxsize=None)
def validator(schema_file: str) -> Draft202012Validator:
    schema = _registry().contents(BASE_URI + schema_file)
    return Draft202012Validator(schema, registry=_registry(), format_checker=FormatChecker())


def errors(document: dict, schema_file: str) -> list[tuple[str, str]]:
    """[(json_pointer, message)] sorted for stable reporting; empty when valid."""
    out = []
    for err in validator(schema_file).iter_errors(document):
        pointer = "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in err.absolute_path)
        out.append((pointer if pointer != "/" else "", err.message))
    return sorted(set(out))


def require_valid(document: dict, schema_version: str) -> None:
    if not isinstance(document, dict):
        raise CompositionError("invalid_json", "document must be a JSON object")
    found = document.get("schema_version")
    if found != schema_version:
        raise CompositionError("unsupported_schema_version",
                               f"expected {schema_version}, got {found!r}", ["/schema_version"])
    problems = errors(document, SCHEMA_VERSIONS[schema_version])
    if problems:
        raise CompositionError(
            "invalid_" + schema_version.replace("cs-", "").replace("-v1", "").replace("-", "_"),
            "; ".join(f"{p or '/'}: {m}" for p, m in problems[:10]),
            [p or "/" for p, _ in problems])
