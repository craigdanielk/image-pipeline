#!/usr/bin/env python3
"""Knowledge loader — dep-free JSON libraries under knowledge/."""
from __future__ import annotations

import json
import os
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_KNOWLEDGE_ROOT = os.path.join(_ROOT, "knowledge")


class KnowledgeError(FileNotFoundError):
    """Raised when a knowledge entry cannot be loaded."""


def knowledge_root() -> str:
    return _KNOWLEDGE_ROOT


def _load_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise KnowledgeError(f"Knowledge file must be a JSON object: {path}")
    return data


def resolve_path(rel: str) -> str:
    """Resolve path relative to image-pipeline root or knowledge/."""
    if os.path.isabs(rel):
        return rel
    candidates = [
        os.path.join(_ROOT, rel),
        os.path.join(_KNOWLEDGE_ROOT, rel),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    raise KnowledgeError(f"Knowledge not found: {rel}")


def load_product(slug: str) -> dict[str, Any]:
    return _load_json(os.path.join(_KNOWLEDGE_ROOT, "products", f"{slug}.json"))


def load_campaign(slug: str) -> dict[str, Any]:
    return _load_json(os.path.join(_KNOWLEDGE_ROOT, "campaigns", f"{slug}.json"))


def load_operation(op_id: str) -> dict[str, Any]:
    return _load_json(os.path.join(_KNOWLEDGE_ROOT, "operations", f"{op_id}.json"))


def load_constraint(constraint_id: str) -> dict[str, Any]:
    return _load_json(os.path.join(_KNOWLEDGE_ROOT, "constraints", f"{constraint_id}.json"))


def load_provider(provider_id: str) -> dict[str, Any]:
    return _load_json(os.path.join(_KNOWLEDGE_ROOT, "providers", f"{provider_id}.json"))


def list_operations() -> list[str]:
    ops_dir = os.path.join(_KNOWLEDGE_ROOT, "operations")
    if not os.path.isdir(ops_dir):
        return []
    return sorted(
        name[:-5]
        for name in os.listdir(ops_dir)
        if name.endswith(".json")
    )
