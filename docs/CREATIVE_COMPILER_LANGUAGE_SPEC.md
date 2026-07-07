# Creative Compiler Language Specification

**Version:** 1.0 (2026-07-07)  
**Service:** `services/aragon/image-pipeline`  
**Normative schemas:** `schemas/*.schema.json`  
**Status:** Specification — partial implementation (see §Implementation status)

---

## Foundational invariant

> **The runtime never compiles. The compiler never executes.**

| Phase | Responsibility | Must not |
|-------|----------------|----------|
| **Extract** | Source artifacts → specification languages | Call providers |
| **Compile** | Specifications → `ProviderPrompt` | Execute graph nodes |
| **Plan** | `ProviderPrompt` → `ProviderExecutionPlan` | Mutate specifications |
| **Execute** | Graph IR + plans → adapters → outputs | Read `cad.md`, lower `job-v1`, invoke `PromptCompiler` |

Everything in this document is a consequence of that invariant.

---

## What this service is

This is **not** an AI image generator. It is a **deterministic creative compiler**:

- **Source languages** — human/agent-authored inputs (`cad.md`, `job-v1`)
- **Specification languages** — versioned, content-addressed truth (`ProductSpec`, `CreativeSpec`, `EditSpec`, `ExecutionRequest`)
- **Intermediate representations** — compiler and planner output (`ProviderPrompt`, `ProviderExecutionPlan`, Graph IR)
- **Backends** — provider adapters (Higgsfield today; Flux, GPT Image, Veo, fal, etc. later)

Prompt engineering is a **source-authoring** activity. Runtime never sees raw prompts as input.

---

## Language tiers

### Source languages (authoring only — never runtime input)

| Source | Lowers to | Notes |
|--------|-----------|-------|
| `cad.md` | `ProductSpec` (+ partial `CreativeSpec` via extractor) | Engineering extraction input. **Archived after extract.** Markdown must not cross the compile boundary. |
| `job-v1` | `CreativeSpec` + `EditSpec` + `ExecutionRequest` | Transitional convenience format. **Becomes source-only; runtime will refuse it.** |

### Specification languages (machine canonical)

| Language | Schema | Scope | Mutability |
|----------|--------|-------|------------|
| **ProductSpec** | `product-spec-v1` | Object truth — geometry, materials, brand marks, asset bindings | Immutable per product version |
| **CreativeSpec** | `creative-spec-v1` | Campaign truth — scene locks, default composition, base template | Per campaign; versioned |
| **EditSpec** | `edit-spec-v1` | Per-run surgical delta — logo fix, region replace, mark insertion | Per run; may chain |
| **ExecutionRequest** | `execution-request-v1` | Per-run execution parameters — engine family, refs, output dims, dry_run | Per run |

### Intermediate representations (compiler/planner output — runtime input)

| IR | Schema | Produced by | Consumed by |
|----|--------|-------------|-------------|
| **ProviderPrompt** | `provider-prompt-v1` | `PromptCompiler` | `CapabilityResolver` |
| **ProviderExecutionPlan** | `provider-execution-plan-v1` | `CapabilityResolver` | `ProviderAdapter` |
| **Graph IR** | `graph-v1` | Graph compiler / author | Graph runtime |

**Seven languages.** Four specifications + three IRs. Everything else is translation between them.

---

## End-state compiler chain

```
cad.md                          job-v1 (source, transitional)
  │                               │
  ▼                               ▼
Product Extractor              Spec Lowering
  │                               │
  ▼                               ├──► CreativeSpec
ProductSpec                     ├──► EditSpec (0..n, ordered)
  │                               └──► ExecutionRequest
  │                                       │
  └───────────────┬───────────────────────┘
                  ▼
            PromptCompiler          (deterministic, no LLM)
                  │
                  ▼
            ProviderPrompt          (semantic intent — provider-agnostic)
                  │
                  ▼
          CapabilityResolver        (model registry + provider capabilities)
                  │
                  ▼
        ProviderExecutionPlan       (LLVM backend — concrete invocation)
                  │
                  ▼
              Graph IR              (operational DAG — no business knowledge)
                  │
                  ▼
            Graph Runtime
                  │
                  ▼
           ProviderAdapter          (Higgsfield, Flux, GPT Image, Veo, …)
                  │
                  ▼
         OutputNormalizer → Image
```

---

## Language definitions

### 1. ProductSpec (`product-spec-v1`)

**Purpose:** Immutable object truth for a product version.

**Answers:** What is this object? What are its materials, dimensions, brand marks, and canonical asset bindings?

**Lowering:** `Product Extractor.extract(cad.md)` → `ProductSpec`. `cad.md` is archived; runtime reads `ProductSpec` only.

**Required fields:** `schema_version`, `id`, `name`, `geometry_text`

**Key fields:**

| Field | Role |
|-------|------|
| `geometry_text` | Canonical object-truth prose (interim; evolves toward structured `geometry_sections`) |
| `geometry_sections` | Structured geometry blocks (dimensions, materials, zones) |
| `brand_marks` | Declarative mark definitions with asset bindings and placement rules |
| `asset_refs` | Canonical reference assets (flatlay, crest, plate set) |
| `immutable` | Must be `true` |
| `source.content_hash` | SHA256 of source `cad.md` at extract time |

**Invariants:**

- Content-addressed: identical source → identical `ProductSpec` hash.
- Never mutated per campaign or per run.
- Never contains scene locks, surgical edits, or engine parameters.

**Forbidden:** `prompt_template`, `engine`, `reference`, scene-specific kid-swap rules.

**Schema:** `schemas/product-spec-v1.schema.json`  
**Example:** `schemas/examples/product-spec-primikey-usba.json`

---

### 2. CreativeSpec (`creative-spec-v1`)

**Purpose:** Campaign-level creative truth.

**Answers:** How should this product appear in this campaign? What scene locks and base composition apply to every run in the campaign?

**Lowering:** Authored directly, or derived from `cad.md` §2–§6 via `extract_creative_from_cad()`, or lowered from `job-v1` campaign fields.

**Required fields:** `schema_version`, `id`, `product_spec_id`, `prompt_template`

**Key fields:**

| Field | Role |
|-------|------|
| `prompt_template` | Base campaign template with `{placeholder}` slots — **not** surgical edit prose |
| `negative_template` | Campaign-wide negative guardrails |
| `scene_locks` | Per-scene invariant blocks (machine-readable; compiled into prompt sections) |
| `run_invariants` | Campaign-wide execution invariants |
| `default_subject` | Default `{view}` and subject placeholders |
| `default_composition`, `default_background` | Campaign defaults |

**Invariants:**

- References exactly one `product_spec_id`.
- Changes per campaign, not per run.
- Must not contain per-run surgical deltas (those belong in `EditSpec`).

**Forbidden:** Engine model IDs, provider CLI params, parent output references for logo-fix chains.

**Schema:** `schemas/creative-spec-v1.schema.json`  
**Example:** `schemas/examples/creative-spec-primikey-usba.json`

---

### 3. EditSpec (`edit-spec-v1`)

**Purpose:** Per-run surgical modification — the delta applied on top of campaign truth.

**Answers:** What single change applies to this execution? What must be locked? What must be excluded?

**Lowering:** Authored per iteration, or lowered from `job-v1` when `prompt_template` contains surgical "ONLY CHANGE" blocks (migration path). Chains via `parent_asset_id` / `parent_run_id`.

**Required fields:** `schema_version`, `id`, `operation`, `parent_asset_id`

**Operations (v1 enum):**

| Operation | Meaning |
|-----------|---------|
| `lock_and_rerender` | Re-render parent output in place; lock all but declared deltas |
| `replace_mark` | Replace chest/kit mark per constraint rules |
| `remove_element` | Remove named visual element (swoosh, wrong logo, artifact) |
| `insert_mark` | Insert brand mark at declared region |
| `region_edit` | Surgical edit within a semantic or normalised region |
| `subject_swap` | Replace subject class (e.g. adult → child) with pose lock |

**Key fields:**

| Field | Role |
|-------|------|
| `parent_asset_id` | Content-addressed handle of parent output or anchor |
| `parent_run_id` | Optional lineage link to prior execution |
| `constraints` | Machine-evaluable rules compiled into prompt sections |
| `exclusions` | Hard negations ("no hole", "no crown", "no Joma J") |
| `regions` | Semantic region IDs or normalised `{x,y,w,h}` hints |
| `reference_roles` | Asset role bindings for this edit (pose, product, logo) |

**Invariants:**

- 0..n EditSpecs per compile, applied in order.
- Imperative prompt paragraphs ("ONLY CHANGE — chest branding: …") are **invalid** as a substitute for structured `constraints` + `exclusions`.
- Must not redefine product geometry (that is `ProductSpec`).

**Forbidden:** Full campaign templates, engine selection, output dimensions.

**Schema:** `schemas/edit-spec-v1.schema.json`  
**Example:** `schemas/examples/edit-spec-hairbands-logo-fix.json`

---

### 4. ExecutionRequest (`execution-request-v1`)

**Purpose:** Per-run execution parameters.

**Answers:** Which engine family and model? Which reference assets? What output dimensions? Dry run?

**Lowering:** `spec_compiler.compile_execution_request(job-v1)` or authored directly.

**Required fields:** `schema_version`, `id`, `engine`, `output`

**Key fields:**

| Field | Role |
|-------|------|
| `engine.name` | Adapter family (`higgsfield`, `fal`, `openai`, …) |
| `engine.model` | Provider-native model ID (resolved by CapabilityResolver, not interpreted by compiler) |
| `reference` | Anchor asset(s) for this run |
| `output` | `imgtype`, `aspect_ratio`, `resolution`, `count`, `composition`, `background` |
| `dry_run` | Cost estimate only |
| `parent_run_id` | Lineage |
| `provenance` | Source artifact tracking |

**Invariants:**

- Contains **no** creative prose. Subject keys are placeholder values only.
- `engine.model` is opaque to `PromptCompiler`; planning happens in `CapabilityResolver`.

**Forbidden:** `prompt_template`, `geometry_text`, surgical edit prose.

**Schema:** `schemas/execution-request-v1.schema.json`  
**Example:** `schemas/examples/execution-request-primikey-scene-float-a.json`

---

### 5. ProviderPrompt (`provider-prompt-v1`)

**Purpose:** Normalised semantic generation intent — the sole output of `PromptCompiler`.

**Answers:** What should the provider generate, in provider-agnostic terms?

**Lowering:** `PromptCompiler.compile(product, creative, edits[], execution)` → `ProviderPrompt`

**Required fields:** `schema_version`, `positive`, `metadata`, `input_assets`

**Key fields:**

| Field | Role |
|-------|------|
| `positive` | Compiled positive prompt string |
| `negative` | Compiled negative prompt (from `CreativeSpec.negative_template` + `EditSpec.exclusions`) |
| `metadata.content_hash` | SHA256 cache key |
| `metadata.execution_id` | Links to `ExecutionRequest.id` |
| `input_assets.references` | Resolved reference paths/handles |
| `input_assets.input_images` | Additional input image paths |

**Invariants:**

- **Deterministic:** identical compile inputs → identical `positive`, `negative`, `content_hash`.
- Provider-agnostic: no CLI flags, no model IDs, no upload mechanics.
- Runtime compile inputs are **never** raw `job-v1` or `cad.md` in end state.

**Forbidden:** `engine.model`, CLI params, `media_input` IDs, provider URLs.

**Schema:** `schemas/provider-prompt-v1.schema.json`  
**Example:** `schemas/examples/provider-prompt-primikey-scene-float-a.json`

---

### 6. ProviderExecutionPlan (`provider-execution-plan-v1`)

**Purpose:** Concrete provider invocation plan — the LLVM backend.

**Answers:** Exactly how does this provider execute the semantic intent?

**Lowering:** `CapabilityResolver.resolve(provider_prompt, execution_request, capability_registry)` → `ProviderExecutionPlan`

**Required fields:** `schema_version`, `id`, `provider`, `model`, `method`, `params`

**Key fields:**

| Field | Role |
|-------|------|
| `provider` | Adapter id (`higgsfield`, `fal`, …) |
| `model` | Provider-native model identifier |
| `method` | API operation (`generate`, `edit`, `cost_estimate`, …) |
| `params` | Provider-specific flags (aspect_ratio, resolution, batch_size, …) |
| `uploads` | Normalised upload steps `{local_path → provider_media_id}` |
| `media_inputs` | Provider-native input image descriptors |
| `retries` | Retry policy |
| `masks` | Region masks when provider supports them |

**Invariants:**

- Derived deterministically from `ProviderPrompt` + `ExecutionRequest` + capability registry.
- Contains **no** brand rules, scene locks, or edit semantics.
- Only layer that knows CLI/API mechanics.

**Forbidden:** `prompt_template`, `geometry_text`, `scene_locks`, business knowledge.

**Schema:** `schemas/provider-execution-plan-v1.schema.json`

---

### 7. Graph IR (`graph-v1`)

**Purpose:** Operational execution DAG — how to run the pipeline, not what to render.

**Answers:** In what order do intake, validate, compile, plan, resolve, generate, and persist execute?

**Lowering:** `graph_compiler.compile(execution_request)` or authored directly for complex DAGs.

**Node types (v1):**

| Type | Role |
|------|------|
| `intake.load` | Load persisted compile artifacts or source paths |
| `jobspec.validate` | Validate inputs (transitional — retired when runtime refuses `job-v1`) |
| `prompt.build` | Invoke `PromptCompiler` → `ProviderPrompt` |
| `capability.resolve` | Invoke `CapabilityResolver` → `ProviderExecutionPlan` |
| `reference.resolve` | Resolve asset handles to local paths |
| `engine.generate` | Execute plan via provider adapter |
| `output.persist` | Normalise and persist outputs |

**Invariants:**

- DAG only — no cycles.
- Typed ports between nodes (`schemas/nodes/*.schema.json`).
- **No business knowledge** in node params. Graph metadata carries **handles and hashes only** — not full `job_snapshot` blobs (end state).
- Review gates (`review.wait`) are future node types.

**Forbidden:** `prompt_template`, `cad.md` paths, brand rules in node `params`.

**Schema:** `schemas/graph-v1.schema.json`  
**Example:** `schemas/examples/graph-v1-linear-pipeline.json`

---

## Lowering table

| From | To | Translator | Phase |
|------|-----|------------|-------|
| `cad.md` | `ProductSpec` | `Product Extractor` | Extract |
| `cad.md` §2–§6 | `CreativeSpec` | `extract_creative_from_cad()` | Extract |
| `job-v1` | `CreativeSpec` | `derive_creative_from_job()` | Extract |
| `job-v1` | `EditSpec` | `derive_edit_from_job()` (future) | Extract |
| `job-v1` | `ExecutionRequest` | `compile_execution_request()` | Extract |
| `ProductSpec` + `CreativeSpec` + `EditSpec[]` + `ExecutionRequest` | `ProviderPrompt` | `PromptCompiler` | Compile |
| `ProviderPrompt` + `ExecutionRequest` | `ProviderExecutionPlan` | `CapabilityResolver` | Plan |
| `ExecutionRequest` | `graph-v1` | `graph_compiler` | Plan |
| `ProviderExecutionPlan` | Provider response | `ProviderAdapter` | Execute |
| Provider response | Canonical output | `OutputNormalizer` | Execute |

---

## Asset registry (cross-cutting)

Paths in specifications lower to **content-addressed asset handles**:

```
{ "asset_id": "sha256:…", "role": "target_anchor", "uri": "…" }
```

The asset registry is not a language tier — it is infrastructure shared by Extract, Compile, and Execute. Reference resolution, lineage (`parent_asset_id`), and generate-time cache keys all depend on it.

---

## Capability registry (cross-cutting)

The capability registry maps `(provider, model)` → supported features:

- Accepts `--image` / reference input?
- Supports `batch_size`, `model_type`, `colors`, `resolution` values?
- Requires upload normalisation for HTTP URLs?
- Supports negative prompt param?

`CapabilityResolver` is the only consumer. Specifications and `ProviderPrompt` remain provider-agnostic.

---

## Determinism contract

| Stage | Guarantee |
|-------|-----------|
| Extract | Same `cad.md` bytes → same `ProductSpec` |
| Compile | Same spec triple + edits + execution → same `ProviderPrompt.content_hash` |
| Plan | Same `ProviderPrompt` + `ExecutionRequest` + registry version → same `ProviderExecutionPlan` |
| Execute | Provider nondeterminism excepted; same plan + same asset hashes → reproducible cost estimate |

Compiled artifacts are persisted (`compile_artifacts` table) and keyed by `content_hash`. Generate-time cache keys extend the hash with asset content hashes and engine params.

---

## job-v1 sunset

`job-v1` is **source code**, not runtime input.

| Milestone | Behaviour |
|-----------|-----------|
| **Now (transitional)** | `run_job.py` / `run_graph.py` accept `job-v1`; compiler lowers inline via `compile_from_job_v1()` |
| **Next** | CLI requires pre-compiled `ProviderPrompt` + `ExecutionRequest`; `job-v1` lowered at authoring time |
| **End state** | Runtime refuses `job-v1`; graph executor accepts only persisted compile artifacts + plans |

---

## Language v2 (infra — 2026-07-07)

**Status:** Schemas, knowledge repository, validation gate, and `PromptCompiler v2` implemented. **Not wired to `run_job` / graph execution.** No JOGA jobs through pipeline until v2 authoring is complete.

### v2 specification languages

| Language | Schema | Key change from v1 |
|----------|--------|-------------------|
| **ProductSpec** | `product-spec-v2` | Structured `product.geometry`, `brand.logos[]`, `editing` regions. **`geometry_text` forbidden.** |
| **CreativeSpec** | `creative-spec-v2` | Structured `scene`, `scene_locks`. **`prompt_template` forbidden.** |
| **EditSpec** | `edit-spec-v2` | Typed `operations[]` + `constraints.preserve/forbidden`. **No `ONLY CHANGE` prose.** |

### Knowledge repository

```
knowledge/
  products/       # product ontology JSON
  campaigns/      # campaign truth JSON
  operations/     # op templates (compiler-internal prompt_fragment)
  constraints/    # reusable preserve fragments
  providers/      # provider metadata (not secrets)
```

Canonical instances: `knowledge/products/hairbands.json`, `knowledge/campaigns/starz-kid.json`.

### v2 compile chain (infra CLI)

```
ProductSpec v2 + CreativeSpec v2 + EditSpec v2[] + ExecutionRequest v1
  → compile_validator.validate_for_compile()   # fail hard
  → prompt_compiler_v2.compile_v2()            # → ProviderPrompt v1
```

CLI (no execution):

```bash
cd image-pipeline/core
python3 compile_specs_v2.py \
  --product ../knowledge/products/hairbands.json \
  --creative ../knowledge/campaigns/starz-kid.json \
  --execution ../schemas/examples/execution-request-hairbands-logo-fix.json \
  --edit ../schemas/examples/edit-spec-v2-hairbands-logo-fix.json
```

### Golden exit criterion (L7)

`schemas/examples/edit-spec-v2-hairbands-logo-fix.json` expresses logo-fix as structured ops only. Validation rejects `geometry_text`, `prompt_template`, and `ONLY CHANGE` anywhere in the spec tree.

---

## Implementation status (2026-07-07)

| Language / component | Schema | Translator | Runtime wired |
|---------------------|--------|------------|---------------|
| ProductSpec v1 | ✓ | ✓ extractor | Transitional (job-v1 path) |
| ProductSpec v2 | ✓ | ✓ knowledge | **Infra only** |
| CreativeSpec v1 | ✓ | ✓ derive | Transitional |
| CreativeSpec v2 | ✓ | ✓ knowledge | **Infra only** |
| EditSpec v1 | ✓ | regex heuristic | Transitional |
| EditSpec v2 | ✓ | ✓ structured ops | **Infra only** |
| ExecutionRequest | ✓ | ✓ | Partial |
| ProviderPrompt | ✓ | ✓ v1 + v2 compilers | Partial |
| ProviderExecutionPlan | ✓ | ✓ CapabilityResolver | Wired (job-v1 path) |
| Graph IR | ✓ | ✓ graph_compiler | Execute-only 4-node |
| Knowledge repo | ✓ | ✓ knowledge_loader | Compile-time only |
| Compile gate | — | ✓ compile_validator | v2 path |
| Asset registry | — | — | Not implemented |

See `docs/COMPILER_FOUNDATIONS.md` for module map and `docs/RUNTIME.md` for current graph execution.

---

## Related documents

| Document | Role |
|----------|------|
| `README.md` | Operator entry — invariant, quickstart, transitional `job-v1` usage |
| `docs/COMPILER_FOUNDATIONS.md` | Module map and compile order |
| `docs/RUNTIME.md` | Graph runtime behaviour |
| `docs/GRAPH_MIGRATION_BASELINE.md` | job-v1 freeze + design-time/runtime boundary |
| `WORKFLOW.md` | Node sequence reference |
| `schemas/*.schema.json` | Normative machine definitions |
