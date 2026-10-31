"""Typed refusals for composition, export and handoff.

Every refusal is a CompositionError carrying the contract's common `error`
shape (code, message, retryable, field_paths, related_ids). Codes reuse the
endpoint-outline vocabulary where one exists (font_asset_missing,
asset_hash_mismatch, scope_mismatch, ...). Nothing is downgraded to a warning.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# HTTP status per endpoint-outline.md "Refusal codes"; anything else is 422.
_STATUS = {
    "scope_mismatch": 403,
    "rights_denied": 403,
    "resource_unavailable": 404,
    "revision_conflict": 409,
    "idempotency_conflict": 409,
    "source_unavailable": 409,
    "unsupported_schema_version": 400,
    "invalid_json": 400,
}


@dataclass
class CompositionError(Exception):
    code: str
    message: str
    field_paths: list[str] = field(default_factory=list)
    related_ids: list[str] = field(default_factory=list)
    retryable: bool = False

    def __post_init__(self) -> None:
        super().__init__(f"{self.code}: {self.message}")

    @property
    def http_status(self) -> int:
        return _STATUS.get(self.code, 422)

    def to_error(self) -> dict:
        """The contract's common.schema.json#/$defs/error object."""
        return {
            "code": self.code,
            "message": self.message[:2000],
            "retryable": self.retryable,
            "field_paths": [p[:300] for p in self.field_paths[:30]],
            "related_ids": [r[:100] for r in self.related_ids[:30]],
        }
