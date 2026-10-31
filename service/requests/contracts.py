"""Bundled Creative Studio contract schemas (docs/creative-studio/contracts).

The JSON Schema files are the only schema definition; this module loads them
into a local registry (never fetches the reserved ``$id`` URLs), asserts
formats, rejects unknown fields (the schemas say ``additionalProperties:
false``) and reports a JSON Pointer per violation (contract.md §1).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "docs" / "creative-studio" / "contracts"
BASE_ID = "https://schemas.creative-studio.invalid/v1/"

# schema_version constant -> schema file
SCHEMA_FILES = {
    "brand-package-v1": "brand-package-v1.schema.json",
    "cs-creative-request-v1": "creative-request-v1.schema.json",
    "cs-treatment-v1": "treatment-v1.schema.json",
    "cs-authoring-draft-v1": "authoring-draft-v1.schema.json",
    "cs-operation-v1": "operation-v1.schema.json",
}

_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$")


class ContractViolation(ValueError):
    def __init__(self, schema_version: str, errors: list[tuple[str, str]]):
        self.schema_version = schema_version
        self.errors = errors                    # [(json_pointer, message)]
        super().__init__(f"{schema_version}: " + "; ".join(f"{p or '/'}: {m}" for p, m in errors[:10]))

    @property
    def field_paths(self) -> list[str]:
        return [p or "/" for p, _ in self.errors][:30]


def _format_checker() -> FormatChecker:
    checker = FormatChecker()

    @checker.checks("date-time", raises=ValueError)
    def _date_time(value):
        if not isinstance(value, str):
            return True
        if not _RFC3339.match(value):
            return False
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True

    @checker.checks("uri", raises=ValueError)
    def _uri(value):
        if not isinstance(value, str):
            return True
        parts = urlsplit(value)
        return bool(parts.scheme) and (bool(parts.netloc) or parts.scheme in ("urn", "mailto"))

    return checker


@lru_cache(maxsize=1)
def _registry() -> Registry:
    resources = []
    for path in sorted(CONTRACTS_DIR.glob("*.schema.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        resources.append((doc["$id"], Resource.from_contents(doc)))
    return Registry().with_resources(resources)


@lru_cache(maxsize=None)
def schema(schema_version: str) -> dict:
    try:
        name = SCHEMA_FILES[schema_version]
    except KeyError:
        raise ContractViolation(schema_version, [("/schema_version", "unsupported_schema_version")]) from None
    return json.loads((CONTRACTS_DIR / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _validator(schema_version: str) -> Draft202012Validator:
    return Draft202012Validator(schema(schema_version), registry=_registry(), format_checker=_format_checker())


def def_validator(def_name: str) -> Draft202012Validator:
    """Validator for one common.schema.json $def (e.g. version_ref, approval)."""
    return Draft202012Validator({"$ref": f"{BASE_ID}common.schema.json#/$defs/{def_name}"},
                                registry=_registry(), format_checker=_format_checker())


def errors_for(document, schema_version: str) -> list[tuple[str, str]]:
    found = []
    for err in sorted(_validator(schema_version).iter_errors(document), key=lambda e: list(e.absolute_path)):
        pointer = "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in err.absolute_path)
        found.append((pointer, err.message[:300]))
    return found


def validate(document, schema_version: str | None = None) -> None:
    version = schema_version or (document.get("schema_version") if isinstance(document, dict) else None)
    if version not in SCHEMA_FILES:
        raise ContractViolation(str(version), [("/schema_version", "unsupported_schema_version")])
    errs = errors_for(document, version)
    if errs:
        raise ContractViolation(version, errs)


def validate_def(value, def_name: str) -> None:
    errs = []
    for err in def_validator(def_name).iter_errors(value):
        pointer = "".join("/" + str(p) for p in err.absolute_path)
        errs.append((pointer, err.message[:300]))
    if errs:
        raise ContractViolation(def_name, errs)
