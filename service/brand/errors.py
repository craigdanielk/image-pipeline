"""Typed brand/request refusals (code -> HTTP status per endpoint-outline.md)."""
from __future__ import annotations

_STATUS = {
    "brand_not_approved": 409, "approval_stale": 409, "source_revision_changed": 409, "source_unavailable": 409,
    "unresolved_decision_blocks": 409, "revision_conflict": 409,
    "rights_denied": 403, "rights_expired": 403, "scope_mismatch": 403, "authorization_blocked": 403,
    "asset_hash_mismatch": 422, "font_asset_missing": 422, "brandkit_incomplete": 422, "unsupported_media_type": 422,
    "approval_receipt_invalid": 422, "higgsfield_payload_mismatch": 422, "unsupported_schema_version": 400,
    "invalid_contract": 422, "unsupported_canvas": 422, "copy_selection_required": 422, "claim_unsourced": 422,
    "budget_missing": 422, "offering_geometry_forbidden": 422, "treatment_not_accepted": 409,
    "physical_product_spec_required": 422, "unsupported_offering": 422, "upstream_unavailable": 502,
}


class InputRefusal(Exception):
    """A typed, non-retryable refusal of a brand/campaign input."""

    def __init__(self, code: str, message: str = "", field_paths: list[str] | None = None):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message or code
        self.field_paths = list(field_paths or [])[:30]

    @property
    def status(self) -> int:
        return _STATUS.get(self.code, 422)
