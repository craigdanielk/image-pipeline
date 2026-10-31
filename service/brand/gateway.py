"""Marketing OS gateway seam used by CS-B2 (API-only authority, never product-table reads).

The hosted HTTP client (trusted service authentication + the user's identity,
reusing MOS's brand-studio.ts gateway pattern) is wired in CS-B5. Tests use a
recorded-shape mock. No network call is made by anything in CS-B2.
"""
from __future__ import annotations

from typing import Protocol


class GatewayUnavailable(Exception):
    """The upstream authority could not be reached; the caller may retry within policy."""


class MarketingOsGateway(Protocol):
    def authorize_binding(self, *, user_id: str, mos_workspace_id: str) -> dict:
        """MOS's own authorization response for binding this user's Studio workspace to the MOS workspace."""

    def export_brand_kit(self, *, mos_workspace_id: str, artifact_id: str) -> dict:
        """``mos-brand-export-v1`` for one exact BrandKit artifact version (plus MOS's current head)."""

    def fetch_asset(self, *, mos_workspace_id: str, ref: dict, purpose: str, max_bytes: int) -> bytes:
        """Bytes of one exact MOS asset version via a fresh, scoped download ticket."""

    def campaign_sources(self, *, mos_workspace_id: str, visual_request: dict) -> dict:
        """``mos-campaign-sources-v1``: immutable source versions for one visual request."""

    def eligibility(self, *, mos_workspace_id: str, user_id: str, action: str, source_versions: list) -> dict:
        """EligibilityReceipt (common.schema.json) from MOS's own policy, <=60 s validity."""


class ObjectWriter(Protocol):
    def put(self, storage_key: str, data: bytes, mime_type: str) -> None:
        """Write bytes at the exact private object key as the runtime principal (no upsert)."""
