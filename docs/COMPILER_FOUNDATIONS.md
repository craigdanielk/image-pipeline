# Compiler Foundations — Creative Compiler Model

**BRIEF:** `BRIEF::compiler-foundations-three-spec-model::2026-07-07` (#29804)  
**Language spec:** [`CREATIVE_COMPILER_LANGUAGE_SPEC.md`](./CREATIVE_COMPILER_LANGUAGE_SPEC.md)  
**Status:** Schemas + partial runtime — see language spec §Implementation status.

## Overview

The image pipeline is a **deterministic creative compiler**. Canonical language
definition: [`CREATIVE_COMPILER_LANGUAGE_SPEC.md`](./CREATIVE_COMPILER_LANGUAGE_SPEC.md).

```
ProductSpec + CreativeSpec + EditSpec[] + ExecutionRequest
  → PromptCompiler → ProviderPrompt
  → CapabilityResolver → ProviderExecutionPlan
  → Graph Runtime → ProviderAdapter
```

> **The runtime never compiles. The compiler never executes.**

| Spec | Scope | Mutability | Source |
|------|-------|------------|--------|
| **ProductSpec** | Geometry, brand marks, asset refs | Immutable | `spec_extractor.extract_product_spec(cad.md)` |
| **CreativeSpec** | Scene locks, campaign invariants, base template | Per campaign | Authored or `extract_creative_from_cad()` |
| **EditSpec** | Per-run surgical delta (logo fix, subject swap) | Per run | Authored or lowered from `job-v1` (future) |
| **ExecutionRequest** | Engine, refs, output, dry_run | Per run | `spec_compiler.compile_execution_request(job-v1)` |
| **ProviderPrompt** | Normalized semantic intent | Per compile | `prompt_compiler.compile()` output |
| **ProviderExecutionPlan** | Concrete provider invocation | Per plan | `CapabilityResolver` (not yet implemented) |

## Compile Order

1. **Extract** product truth from `cad.md` → `ProductSpec v1` (archive `cad.md`)
2. **Author** or derive campaign locks → `CreativeSpec v1`
3. **Author** per-run surgical delta → `EditSpec v1` (0..n, ordered)
4. **Lower** run parameters from `job-v1` (transitional) → `ExecutionRequest v1`
5. **Compile** spec inputs → `ProviderPrompt v1` (deterministic, content-addressed)
6. **Plan** → `ProviderExecutionPlan v1` via `CapabilityResolver` (future)

## Module Map

| Module | Entry point | Output |
|--------|-------------|--------|
| `core/spec_extractor.py` | `extract_product_spec(path)` | `ProductSpec` |
| `core/spec_extractor.py` | `extract_creative_from_cad(path, ...)` | `CreativeSpec` |
| `core/spec_compiler.py` | `compile_execution_request(job)` | `ExecutionRequest` |
| `core/spec_compiler.py` | `derive_creative_from_job(job, ...)` | `CreativeSpec` |
| `core/prompt_compiler.py` | `compile(product, creative, execution)` | `ProviderPrompt` |
| `core/prompt_compiler.py` | `compile_from_job_v1(job)` | `ProviderPrompt` (full lowering) |
| `core/run_job.py` | `build_prompt(job)` | delegates to `compile_positive()` |

## cad.md → Spec Mapping

| cad.md section | Target spec | Field |
|----------------|-------------|-------|
| Header asset refs | ProductSpec | `asset_refs`, `brand_marks` |
| §1 Product geometry | ProductSpec | `geometry_text` |
| §2–§5 Scene locks | CreativeSpec | `scene_locks[]` |
| §6 Kid swap invariants | CreativeSpec | `kid_swap_invariants[]` |
| §7 Job map | (documentation only) | — |

**Runtime rule:** No graph node or scheduler reads `cad.md` directly. All prompt assembly flows through `PromptCompiler`.

## job-v1 → ExecutionRequest Mapping

| job-v1 field | ExecutionRequest field |
|--------------|------------------------|
| `id` | `id` |
| `subject` | `subject` |
| `reference` | `reference` |
| `output` | `output` (with defaults) |
| `engine` | `engine` |
| `dry_run` | `dry_run` |
| `parent_run_id` | `parent_run_id` |
| — | `provenance.compiled_from = "job-v1"` |

`compile_from_job_v1()` additionally loads `spec.cad` as inline `ProductSpec.geometry_text` for backwards-compatible `{cad}` substitution (full file content, matching legacy `build_prompt()`).

## Schemas

Published under `schemas/`:

- `product-spec-v1.schema.json`
- `creative-spec-v1.schema.json`
- `edit-spec-v1.schema.json`
- `execution-request-v1.schema.json`
- `provider-prompt-v1.schema.json`
- `provider-execution-plan-v1.schema.json`
- `graph-v1.schema.json`

Example fixtures: `schemas/examples/`

## Determinism Contract

`prompt_compiler.compile()` with identical triple inputs produces:

- Identical `positive` string
- Identical `metadata.content_hash` (SHA256 of canonical JSON)
- Byte-identical serialized `ProviderPrompt`

## Tests

```bash
cd services/image-pipeline
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Key tests:

- `test_prompt_compiler.py` — determinism + all canonical jobs → ExecutionRequest
- `test_spec_extractor.py` — engineering `cad.md` → valid ProductSpec; scene-lock fixture

## Consumer deployments

Brand-specific job libraries (e.g. e-commerce catalogue work) live under
`tenants/<slug>/tools/image-pipeline/` and **consume** this service. They are not
embedded in canonical tests, schemas/examples, or core modules.

## Downstream Consumers (not in this brief)

- Node Registry `prompt.build` handler → calls `PromptCompiler`
- `graph_compiler.py` → unchanged behavior; may annotate `ExecutionRequest` in metadata later
- `run_graph.py` scheduler → consumes `ProviderPrompt`, not raw templates

## Migration Position

Per migration plan (Control Tower `docs/projects/joga-image-pipeline-v1/`):

1. **Consumer job libraries** stay in `tenants/<slug>/` — not merged into core
2. **Compiler Foundations** ✓
3. **Node Registry** ✓
4. Graph Runtime (`run_graph.py`) — next
