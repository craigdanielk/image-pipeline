"""Validation against the frozen contract schemas in docs/creative-studio/contracts.

Contracts are law: nothing here re-declares a schema. Each file is loaded once
into a referencing registry keyed by its $id (no network fetch, ever).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "docs" / "creative-studio" / "contracts"
ID_BASE = "https://schemas.creative-studio.invalid/v1/"


class ContractViolation(ValueError):
    def __init__(self, schema: str, errors: list[str]):
        super().__init__(f"{schema}: " + "; ".join(errors[:5]))
        self.schema = schema
        self.errors = errors


@lru_cache(maxsize=1)
def _registry() -> Registry:
    resources = []
    for path in sorted(CONTRACTS_DIR.glob("*.schema.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        resources.append((doc["$id"], Resource.from_contents(doc)))
    return Registry().with_resources(resources)


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    schema = _registry().get_or_retrieve(ID_BASE + name).value.contents
    return Draft202012Validator(schema, registry=_registry(), format_checker=FormatChecker())


def validate(name: str, document) -> None:
    """name like 'provider-receipt-v1.schema.json'. Raises ContractViolation with JSON Pointers."""
    errors = sorted(_validator(name).iter_errors(document), key=lambda e: list(e.absolute_path))
    if errors:
        raise ContractViolation(name, ["/" + "/".join(str(p) for p in e.absolute_path) + f": {e.message[:200]}"
                                       for e in errors])


def validate_subschema(name: str, pointer: str, document) -> None:
    """Validate against a $defs / nested location, e.g. a single catalogue entry."""
    ref = {"$ref": ID_BASE + name + "#" + pointer}
    validator = Draft202012Validator(ref, registry=_registry(), format_checker=FormatChecker())
    errors = list(validator.iter_errors(document))
    if errors:
        raise ContractViolation(f"{name}#{pointer}", ["/" + "/".join(str(p) for p in e.absolute_path)
                                                      + f": {e.message[:200]}" for e in errors])
