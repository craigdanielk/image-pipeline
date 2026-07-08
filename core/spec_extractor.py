#!/usr/bin/env python3
"""Product extractor — deterministic cad.md → ProductSpec v1 (no LLM)."""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

import spec_validator

_SECTION_RE = re.compile(r"^##\s+(?:§\d+\s+)?(.+)$", re.MULTILINE)
_ASSET_RE = re.compile(r"^\*\*(.+?):\*\*\s+`([^`]+)`", re.MULTILINE)
_TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "product"


def _parse_sections(text: str) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    matches = list(_SECTION_RE.finditer(text))
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        section_id = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        sections.append({"section_id": section_id, "title": title, "body": body})
    return sections


def _section_by_prefix(sections: list[dict[str, str]], prefix: str) -> dict[str, str] | None:
    prefix_l = prefix.lower()
    for sec in sections:
        if sec["title"].lower().startswith(prefix_l) or sec["section_id"].startswith(prefix_l):
            return sec
    return None


def extract_product_spec(cad_md_path: str) -> dict[str, Any]:
    """Parse a cad.md file into an immutable ProductSpec v1."""
    path = os.path.abspath(cad_md_path)
    with open(path, encoding="utf-8") as f:
        text = f.read()

    title_match = _TITLE_RE.search(text)
    name = title_match.group(1).strip() if title_match else os.path.basename(os.path.dirname(path))

    header_end = text.find("\n---\n")
    header = text[:header_end] if header_end != -1 else text

    asset_refs: list[dict[str, str]] = []
    brand_marks: list[dict[str, str]] = []
    for match in _ASSET_RE.finditer(header):
        label, ref_path = match.group(1).strip(), match.group(2).strip()
        role = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        entry = {"path": ref_path, "role": role, "label": label}
        asset_refs.append(entry)
        if "crest" in role or "mark" in role or "logo" in role:
            brand_marks.append(entry)

    sections = _parse_sections(text)
    geometry = (
        _section_by_prefix(sections, "product geometry")
        or _section_by_prefix(sections, "§1")
        or _section_by_prefix(sections, "overall envelope")
        or (sections[0] if sections else None)
    )
    geometry_text = geometry["body"] if geometry else ""

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    spec: dict[str, Any] = {
        "schema_version": spec_validator.PRODUCT_SCHEMA,
        "id": _slugify(name),
        "name": name,
        "immutable": True,
        "geometry_text": geometry_text,
        "geometry_sections": sections[:1] if geometry else [],
        "brand_marks": brand_marks,
        "asset_refs": asset_refs,
        "source": {"cad_path": path, "content_hash": content_hash},
    }
    spec_validator.validate_product_spec(spec)
    return spec


def extract_creative_from_cad(
    cad_md_path: str,
    *,
    product_spec_id: str,
    creative_id: str | None = None,
    campaign_id: str | None = None,
    prompt_template: str = "",
) -> dict[str, Any]:
    """Derive CreativeSpec scene locks and invariants from cad.md sections §2–§6."""
    path = os.path.abspath(cad_md_path)
    with open(path, encoding="utf-8") as f:
        text = f.read()

    sections = _parse_sections(text)
    scene_locks: list[dict[str, Any]] = []
    run_invariants: list[str] = []

    for sec in sections:
        title_l = sec["title"].lower()
        if "scene lock" in title_l:
            invariants: dict[str, str] = {}
            for line in sec["body"].splitlines():
                if "|" in line and not line.strip().startswith("|--"):
                    cells = [c.strip() for c in line.split("|") if c.strip()]
                    if len(cells) >= 2 and cells[0].lower() != "invariant":
                        invariants[cells[0]] = cells[1]
            scene_locks.append(
                {
                    "scene_id": sec["section_id"],
                    "title": sec["title"],
                    "invariants": invariants,
                }
            )
        elif "run invariant" in title_l or "kid swap" in title_l:
            for line in sec["body"].splitlines():
                line = line.strip()
                if line.startswith("- "):
                    run_invariants.append(line[2:].strip())

    if creative_id is None:
        creative_id = _slugify(os.path.basename(os.path.dirname(path))) + "-creative"

    spec: dict[str, Any] = {
        "schema_version": spec_validator.CREATIVE_SCHEMA,
        "id": creative_id,
        "product_spec_id": product_spec_id,
        "campaign_id": campaign_id or os.path.basename(os.path.dirname(path)),
        "prompt_template": prompt_template,
        "scene_locks": scene_locks,
        "run_invariants": run_invariants,
        "kid_swap_invariants": run_invariants,
    }
    if prompt_template:
        spec_validator.validate_creative_spec(spec)
    return spec


_EXTRACT_RULES = """You draft a product-spec-v2 JSON object (a deterministic ProductSpec, no prose prompts) \
from reference photos of a physical product, for a creative-image compiler's Product Information \
Management system. Use the Read tool to open each photo path given to you, then look at the images \
and output ONLY a single JSON object shaped like this (fill every field; use a best-guess placeholder \
only when truly unobservable — never omit a required field):

{
  "schema_version": "product-spec-v2",
  "id": "<slug-from-name>",
  "name": "<Product Name>",
  "immutable": true,
  "product": {
    "geometry": {"description": "<what it is: shape, form factor>", "symmetry": "<bilateral|radial|none|...>", "envelope": "<short shape-primitive tag, e.g. torus_loop, rectangular_prism>"},
    "materials": [{"id": "primary", "type": "<material type>", "color": "<dominant color>", "finish": "<matte|glossy|woven|...>", "texture": "<surface texture>"}],
    "dimensions": {}
  },
  "brand": {"logos": []},
  "constraints": {"preserve_geometry": true, "preserve_material": true, "preserve_proportions": true},
  "editing": {"protected_regions": ["<parts of the product that must never change>"], "editable_regions": ["background", "scene"]}
}

Rules:
- schema_version, id, name, product.geometry.description, constraints.*, editing.* are all required — never omit them.
- id must be a lowercase slug: [a-z0-9][a-z0-9-]*.
- Never invent brand names or logos you cannot actually see in the photos — leave brand.logos empty if unsure.
- Output ONLY the JSON object. No markdown fences, no preamble, no explanation."""


class SpecExtractError(RuntimeError):
    pass


async def extract_product_spec_from_photos(
    photo_paths: list[str],
    *,
    name_hint: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Vision-draft a product-spec-v2 dict from reference photos (LLM, not saved/validated)."""
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
    except ImportError as exc:
        raise SpecExtractError(
            "claude-agent-sdk not installed — pip install -r ui/requirements.txt"
        ) from exc

    photos_block = "\n".join(f"- {p}" for p in photo_paths)
    parts = [f"Reference photo file paths (use the Read tool to open each one):\n{photos_block}"]
    if name_hint:
        parts.append(f"Product name hint: {name_hint}")
    if notes:
        parts.append(f"Notes from the operator: {notes}")
    user_msg = "\n\n".join(parts)

    options = ClaudeAgentOptions(
        system_prompt=_EXTRACT_RULES,
        tools=["Read"],
        max_turns=4,
        permission_mode="bypassPermissions",
    )

    text_parts: list[str] = []
    try:
        async for message in query(prompt=user_msg, options=options):
            for block in getattr(message, "content", []) or []:
                text = getattr(block, "text", None)
                if text:
                    text_parts.append(text)
    except Exception as exc:  # claude_agent_sdk raises its own exception hierarchy
        raise SpecExtractError(f"Claude Agent SDK call failed: {exc}") from exc

    raw = "".join(text_parts).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        draft = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SpecExtractError(f"Model did not return valid JSON: {exc}\n{raw[:500]}") from exc
    if not isinstance(draft, dict):
        raise SpecExtractError("Model output must be a JSON object.")
    return draft
