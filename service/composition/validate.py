"""Structural (schema) + semantic validation of cs-composition-v1 (contract.md §3, §10)."""
from __future__ import annotations

from .canonical import artifact_sha256, canonical_bytes, sha256_hex
from .contracts import require_valid
from .errors import CompositionError


def validate_composition(doc: dict, *, check_hash: bool = True) -> None:
    require_valid(doc, "cs-composition-v1")
    if check_hash and doc["sha256"] != artifact_sha256(doc):
        raise CompositionError("artifact_hash_mismatch", "composition sha256 does not match its canonical content",
                               ["/sha256"], [doc["version_id"]])
    seen_boards = set()
    for a_idx, board in enumerate(doc["artboards"]):
        base = f"/artboards/{a_idx}"
        if board["id"] in seen_boards:
            raise CompositionError("invalid_composition", f"duplicate artboard id {board['id']}", [base + "/id"])
        seen_boards.add(board["id"])
        validate_layers(board["layers"], base + "/layers", doc["workspace_id"])


def validate_layers(layers: list[dict], base: str, workspace_id: str) -> None:
    by_id: dict[str, int] = {}
    for i, layer in enumerate(layers):
        if layer["id"] in by_id:
            raise CompositionError("invalid_composition", f"duplicate layer id {layer['id']}", [f"{base}/{i}/id"])
        by_id[layer["id"]] = i
    parent: dict[str, str] = {}
    for i, layer in enumerate(layers):
        content = layer["content"]
        if layer["type"] == "group":
            for c_idx, child in enumerate(content["child_ids"]):
                path = f"{base}/{i}/content/child_ids/{c_idx}"
                if child not in by_id:
                    raise CompositionError("invalid_composition", f"group child {child} is not in this artboard",
                                           [path])
                if child == layer["id"] or child in parent:
                    raise CompositionError("invalid_composition", f"layer {child} has more than one parent", [path])
                parent[child] = layer["id"]
        crop = content.get("crop") if isinstance(content, dict) else None
        if crop and (crop["x"] + crop["width"] > 1 or crop["y"] + crop["height"] > 1):
            raise CompositionError("invalid_composition", "crop must stay within the source (x+width<=1, y+height<=1)",
                                   [f"{base}/{i}/content/crop"])
        for key in ("asset", "font"):
            ref = content.get(key) if isinstance(content, dict) else None
            if ref and (ref["workspace_id"] != workspace_id or ref["authority"] != "creative-studio"):
                raise CompositionError("scope_mismatch", f"layer {layer['id']} {key} is not a Studio asset of this "
                                       "workspace", [f"{base}/{i}/content/{key}"])
    for start in parent:  # acyclic
        node, hops = start, 0
        while node in parent:
            node, hops = parent[node], hops + 1
            if hops > len(layers):
                raise CompositionError("invalid_composition", f"group cycle through {start}", [base])
    siblings: dict[str | None, set[int]] = {}
    for i, layer in enumerate(layers):
        key = parent.get(layer["id"])
        bucket = siblings.setdefault(key, set())
        if layer["order"] in bucket:
            raise CompositionError("invalid_composition",
                                   f"layer order {layer['order']} is not unique among siblings", [f"{base}/{i}/order"])
        bucket.add(layer["order"])


def semantic_sha256(doc: dict) -> str:
    """Composition semantic projection (contract.md §3): schema_version, kind, artboards, video,
    with artboards by id and layers by (order, id)."""
    boards = sorted(doc["artboards"], key=lambda b: b["id"])
    boards = [{**b, "layers": sorted(b["layers"], key=lambda l: (l["order"], l["id"]))} for b in boards]
    video = doc["video"]
    if video is not None:
        video = {**video,
                 "shots": sorted(video["shots"], key=lambda s: (s["sequence_start_frame"], s["id"])),
                 "transitions": sorted(video["transitions"], key=lambda t: t["id"]),
                 "overlays": sorted(video["overlays"], key=lambda o: (o["start_frame"], o["id"])),
                 "captions": sorted(video["captions"], key=lambda c: (c["start_frame"], c["id"])),
                 "audio": sorted(video["audio"], key=lambda a: (a["offset_frames"], a["id"]))}
    return sha256_hex(canonical_bytes({"schema_version": doc["schema_version"], "kind": doc["kind"],
                                       "artboards": boards, "video": video}))
