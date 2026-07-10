#!/usr/bin/env python3
"""CLI — compile v2 spec bundle to ProviderPrompt (infra only, no execution)."""
from __future__ import annotations

import argparse
import json
import os
import sys

import prompt_compiler_v2


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile v2 specs → ProviderPrompt JSON")
    parser.add_argument("--product", required=True, help="ProductSpec v2 JSON path")
    parser.add_argument("--creative", required=True, help="CreativeSpec v2 JSON path")
    parser.add_argument("--execution", required=True, help="ExecutionRequest v1 JSON path")
    parser.add_argument("--edit", action="append", default=[], help="EditSpec v2 JSON (repeatable)")
    parser.add_argument("--out", help="Write ProviderPrompt to file (default: stdout)")
    args = parser.parse_args(argv)

    product = _load(args.product)
    creative = _load(args.creative)
    execution = _load(args.execution)
    edits = [_load(p) for p in args.edit]

    provider = prompt_compiler_v2.compile_v2(product, creative, execution, edit_specs=edits)

    payload = json.dumps(provider, indent=2, ensure_ascii=False)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(payload + "\n")
        print(args.out, file=sys.stderr)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
