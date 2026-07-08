#!/usr/bin/env python3
"""Oracle: validate every schemas/examples fixture against its schema (by schema_version)."""
import json
import os
import sys

import jsonschema

ROOT = sys.argv[1] if len(sys.argv) > 1 else "."
SCHEMAS = os.path.join(ROOT, "schemas")
EXAMPLES = os.path.join(SCHEMAS, "examples")

failures = 0
for name in sorted(os.listdir(EXAMPLES)):
    if not name.endswith(".json"):
        continue
    path = os.path.join(EXAMPLES, name)
    with open(path) as f:
        doc = json.load(f)
    sv = doc.get("schema_version")
    if not sv:
        print(f"SKIP  {name}: no schema_version")
        continue
    schema_path = os.path.join(SCHEMAS, f"{sv}.schema.json")
    if not os.path.exists(schema_path):
        print(f"FAIL  {name}: no schema file for schema_version={sv}")
        failures += 1
        continue
    with open(schema_path) as f:
        schema = json.load(f)
    try:
        jsonschema.validate(doc, schema)
        print(f"OK    {name} -> {sv}")
    except jsonschema.ValidationError as e:
        print(f"FAIL  {name} -> {sv}: {e.message} (at {'/'.join(str(p) for p in e.absolute_path)})")
        failures += 1

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)
