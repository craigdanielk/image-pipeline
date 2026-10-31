"""Exact-byte asset access for rendering.

The renderer never resolves "latest", a URL or a path: it asks an AssetSource
for the bytes of one exact AssetRef and verifies the byte hash and workspace
scope itself (contract.md §2.4, §4). The hosted worker supplies a source that
reads Studio-owned private storage; tests use MemoryAssetSource.
"""
from __future__ import annotations

import hashlib
from typing import Protocol

from .errors import CompositionError

# contract.md §10: per-stage media input is limited to 45 MB.
MAX_STAGE_INPUT_BYTES = 45 * 1024 * 1024


class AssetSource(Protocol):
    def fetch(self, ref: dict) -> bytes | None:
        """Bytes stored for (workspace_id, asset_version_id), or None if unavailable."""


class MemoryAssetSource:
    def __init__(self) -> None:
        self._blobs: dict[tuple[str, str], bytes] = {}

    def put(self, ref: dict, data: bytes) -> None:
        self._blobs[(ref["workspace_id"], ref["asset_version_id"])] = data

    def fetch(self, ref: dict) -> bytes | None:
        return self._blobs.get((ref["workspace_id"], ref["asset_version_id"]))


def same_ref(a: dict, b: dict) -> bool:
    keys = ("authority", "workspace_id", "asset_id", "asset_version_id", "revision", "sha256", "mime_type")
    return all(a.get(k) == b.get(k) for k in keys)


def load_exact(source: AssetSource, ref: dict, *, workspace_id: str, role: str, field_path: str,
               missing_code: str = "source_unavailable") -> bytes:
    if ref.get("authority") != "creative-studio" or ref.get("workspace_id") != workspace_id:
        raise CompositionError("scope_mismatch", f"{role} asset is not a Studio-owned asset of this workspace",
                               [field_path])
    data = source.fetch(ref)
    if data is None:
        raise CompositionError(missing_code, f"{role} asset version {ref['asset_version_id']} is not available",
                               [field_path], [ref["asset_version_id"]])
    if len(data) > MAX_STAGE_INPUT_BYTES:
        raise CompositionError("stage_input_too_large", f"{role} asset exceeds the 45 MB stage input bound",
                               [field_path], [ref["asset_version_id"]])
    if hashlib.sha256(data).hexdigest() != ref["sha256"]:
        raise CompositionError("asset_hash_mismatch", f"{role} bytes do not match the pinned sha256",
                               [field_path], [ref["asset_version_id"]])
    return data
