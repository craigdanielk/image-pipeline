"""Generate TypeScript types for the Studio UI from the bundled contract schemas.

    python3 -m service.composition.typegen            # writes ui/app/src/studio/contracts/types.gen.ts
    python3 -m service.composition.typegen --check    # exit 1 if the committed file is stale

The schemas stay the single source of truth; the UI imports only generated
types. Conditional (if/then) refinements are runtime rules enforced by the
service and are not expressed in TypeScript.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .contracts import CONTRACTS_DIR

OUT = Path(__file__).resolve().parents[2] / "ui" / "app" / "src" / "studio" / "contracts" / "types.gen.ts"

# schema file -> exported root type name
ROOTS = {
    "common.schema.json": None,
    "composition-v1.schema.json": "Composition",
    "export-v1.schema.json": "ExportManifestV1",
    "export-v2.schema.json": "ExportManifest",
    "mos-handoff-v1.schema.json": "MosHandoff",
    "mos-ingestion-receipt-v1.schema.json": "MosIngestionReceipt",
    "recipe-v1.schema.json": "Recipe",
    "recipe-command-v1.schema.json": "RecipeCommandBatch",
    "authoring-draft-v1.schema.json": "AuthoringDraft",
    "run-v1.schema.json": "Run",
    "run-event-v1.schema.json": "RunEvent",
    "brand-package-v1.schema.json": "BrandPackage",
}


def _pascal(name: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[^0-9A-Za-z]+", name) if p)


class Gen:
    def __init__(self) -> None:
        self.schemas = {p.name: json.loads(p.read_text()) for p in sorted(CONTRACTS_DIR.glob("*.schema.json"))}
        self.decls: dict[str, str] = {}
        self.order: list[str] = []

    def def_name(self, file: str, def_key: str) -> str:
        prefix = "" if file == "common.schema.json" else _pascal(ROOTS.get(file) or file.split(".")[0])
        return prefix + _pascal(def_key)

    def ref(self, ref: str, current: str) -> str:
        file, _, pointer = ref.partition("#")
        file = file or current
        if not pointer:
            return ROOTS[file]
        parts = pointer.strip("/").split("/")
        if parts[0] == "$defs" and len(parts) == 2:
            name = self.def_name(file, parts[1])
            if name not in self.decls:
                self.decls[name] = ""  # reserve (recursion)
                self.decls[name] = self.ts(self.schemas[file]["$defs"][parts[1]], file, 0)
                self.order.append(name)
            return name
        node = self.schemas[file]
        for p in parts:
            node = node[int(p)] if isinstance(node, list) else node[p]
        return self.ts(node, file, 0)

    def ts(self, node, file: str, depth: int) -> str:
        if node is True or node == {}:
            return "unknown"
        if "$ref" in node:
            return self.ref(node["$ref"], file)
        if "const" in node:
            return json.dumps(node["const"])
        if "enum" in node:
            return " | ".join(json.dumps(v) for v in node["enum"])
        for key in ("anyOf", "oneOf"):
            if key in node:
                return " | ".join(sorted({self.ts(n, file, depth) for n in node[key]}, key=lambda s: (s == "null", s)))
        t = node.get("type")
        if isinstance(t, list):
            return " | ".join(self.ts({**node, "type": x}, file, depth) for x in t)
        if t == "string":
            return "string"
        if t in ("integer", "number"):
            return "number"
        if t == "boolean":
            return "boolean"
        if t == "null":
            return "null"
        if t == "array":
            inner = self.ts(node.get("items", {}), file, depth)
            return f"Array<{inner}>"
        variants = self._discriminated(node)
        if variants:
            return " | ".join(self.ts(v, file, depth) for v in variants)
        if t == "object" or "properties" in node:
            props = node.get("properties", {})
            if not props:
                return "Record<string, unknown>"
            req = set(node.get("required", []))
            pad = "  " * (depth + 1)
            lines = [f"{pad}{json.dumps(k) if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', k) else k}"
                     f"{'' if k in req else '?'}: {self.ts(v, file, depth + 1)};" for k, v in props.items()]
            return "{\n" + "\n".join(lines) + "\n" + "  " * depth + "}"
        return "unknown"

    @staticmethod
    def _discriminated(node) -> list[dict] | None:
        """allOf of `if {D: const X} then {...}` on one discriminator -> one object variant per X."""
        branches = node.get("allOf")
        props = node.get("properties")
        if not branches or not props:
            return None
        keys = set()
        for b in branches:
            cond = (b.get("if") or {}).get("properties") or {}
            if len(cond) != 1 or "const" not in next(iter(cond.values())) or "then" not in b:
                return None
            keys.add(next(iter(cond)))
        if len(keys) != 1:
            return None
        disc = keys.pop()
        out = []
        for b in branches:
            value = b["if"]["properties"][disc]["const"]
            merged = {**props, **b["then"].get("properties", {}), disc: {"const": value}}
            out.append({"type": "object", "properties": merged, "required": node.get("required", [])})
        return out

    def render(self) -> str:
        roots = []
        for file, name in ROOTS.items():
            if name:
                schema = self.schemas[file]
                body = self.ts({k: v for k, v in schema.items() if k != "$defs"}, file, 0)
                roots.append(f"/** {schema.get('title', file)} ({file}) */\nexport type {name} = {body};\n")
        defs = [f"export type {n} = {self.decls[n]};\n" for n in self.order]
        header = ("// GENERATED by `python3 -m service.composition.typegen` from docs/creative-studio/contracts.\n"
                  "// Do not edit. Conditional schema rules are enforced server-side.\n\n")
        return header + "\n".join(roots + defs)


def main(argv: list[str]) -> int:
    text = Gen().render()
    if "--check" in argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(f"{OUT} is stale; run python3 -m service.composition.typegen", file=sys.stderr)
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
