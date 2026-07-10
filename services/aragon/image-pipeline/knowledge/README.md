# Knowledge repository

Canonical structured libraries for the creative compiler. **Machine format on disk: JSON** (dep-free). YAML authoring is equivalent; convert before commit.

## Layout

| Directory | Contents |
|-----------|----------|
| `products/` | Product ontology — geometry, materials, brand logos, editing regions |
| `campaigns/` | Campaign truth — scene, lighting, camera, scene_locks |
| `operations/` | Edit operation templates — `prompt_fragment` keyed by `op` |
| `constraints/` | Reusable constraint fragments for compiler assembly |
| `providers/` | Provider capability metadata (not secrets) |

## Rules

1. Knowledge files are **source libraries**, not runtime input. Compiler loads and resolves into `ProductSpec` / `CreativeSpec` / `EditSpec` v2.
2. No prose blobs in specs — only structured fields + operation templates.
3. `prompt_fragment` in operations/constraints is **compiler-internal**; it never crosses the execute boundary as authoring input.
4. Content-addressed specs reference `knowledge_path` in `source` for traceability.

## Hairbands golden path

```
knowledge/products/hairbands.json
knowledge/campaigns/starz-kid.json
knowledge/operations/replace_logo.json
schemas/examples/edit-spec-v2-hairbands-logo-fix.json
```

Compile chain (infra only — no job execution):

```
ProductSpec v2 + CreativeSpec v2 + EditSpec v2
  → compile_validator (fail hard)
  → PromptCompiler v2 (assemble ProviderPrompt)
  → CapabilityResolver → ProviderExecutionPlan
```

## Adding a product

1. Add `knowledge/products/<slug>.json` matching `product-spec-v2.schema.json`.
2. Add campaign under `knowledge/campaigns/` if new campaign.
3. Add golden example under `schemas/examples/`.
4. Run `python3 -m pytest tests/test_spec_validator_v2.py tests/test_compile_validator.py`.
