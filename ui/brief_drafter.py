#!/usr/bin/env python3
"""LLM-assisted prompt_template drafting for the job brief modal.

Turns a short, structured brief (what the shot should show, mood, optional
feedback on a rejected prior attempt) into a prompt_template string that
follows the house style already established by that product's real job
files — same {cad}/{view} placeholder convention, same tone.
"""
from __future__ import annotations

import os

_MODEL = "claude-sonnet-5"
_MAX_TOKENS = 1024

_TASK_RULES = """You draft the `prompt_template` field of a job-v1 JobSpec for a deterministic \
image-generation compiler. House style, learned from the real example prompts you're given:
- Preserve the literal placeholder tokens {cad} and {view} verbatim wherever the object's \
CAD-derived geometry description or the shot's view/composition description belongs — never \
replace them with actual text, the compiler substitutes them at render time.
- Match the tone, structure, sentence rhythm, and level of detail of the example prompts.
- Do not invent brand names, logos, materials, or claims not present in the brief or CAD spec.
- If feedback on a prior rejected attempt is given, fix exactly that problem without changing \
anything else that wasn't flagged.
- Output ONLY the prompt_template string. No preamble, no markdown fences, no explanation."""


class BriefDraftError(RuntimeError):
    pass


def draft_prompt_template(
    *,
    cad_text: str,
    brief_text: str,
    mood: str | None = None,
    example_prompts: list[str] | None = None,
    feedback: str | None = None,
    system_context: str | None = None,
) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise BriefDraftError(
            "ANTHROPIC_API_KEY is not set — add it to ui/.env to enable AI prompt drafting."
        )
    try:
        import anthropic
    except ImportError as exc:
        raise BriefDraftError(
            "anthropic package not installed — pip install -r ui/requirements.txt"
        ) from exc

    examples = example_prompts or []
    examples_block = "\n\n".join(f"EXAMPLE {i + 1}:\n{p}" for i, p in enumerate(examples[:3]))

    parts = []
    if cad_text:
        parts.append(f"CAD SPEC (object truth — do not restate, just preserve {{cad}}):\n{cad_text[:4000]}")
    if examples_block:
        parts.append(examples_block)
    parts.append(f"BRIEF (what this shot should show):\n{brief_text}")
    if mood:
        parts.append(f"MOOD/STYLE: {mood}")
    if feedback:
        parts.append(f"FEEDBACK ON A PRIOR REJECTED ATTEMPT — fix this: {feedback}")
    user_msg = "\n\n".join(parts)

    system = _TASK_RULES
    if system_context:
        system = (
            f"SYSTEM CONTEXT — the pipeline you're drafting for (read this to understand the "
            f"schema, engine constraints, and house conventions before drafting):\n{system_context}"
            f"\n\n---\n\n{_TASK_RULES}"
        )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as exc:  # anthropic raises its own exception hierarchy
        raise BriefDraftError(f"LLM call failed: {exc}") from exc

    text = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text").strip()
    if not text:
        raise BriefDraftError("Model returned an empty draft.")
    return text
