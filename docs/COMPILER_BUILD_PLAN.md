# Compiler Build Plan — Contract-Driven Unification

**Status:** Active (2026-07-07)  
**Invariant:** The runtime never compiles. The compiler never executes.

Every feature extends a **named contract**, not a module.

---

## Contracts

| Contract | Schema | Producer | Consumer |
|----------|--------|----------|----------|
| Compile manifest | `compile-manifest-v1` | Compiler | Runtime entry |
| ProductSpec | `product-spec-v1` (→ v2 later) | Lowering / extractor | Compiler only |
| CreativeSpec | `creative-spec-v1` | Lowering | Compiler only |
| EditSpec | `edit-spec-v1` | Lowering | Compiler only |
| ExecutionRequest | `execution-request-v1` | Lowering | Compiler + runtime (refs/output) |
| ProviderPrompt | `provider-prompt-v1` | PromptCompiler | Compiler only |
| ProviderExecutionPlan | `provider-execution-plan-v1` | CapabilityResolver | Runtime |
| Graph IR | `graph-v1` | Graph compiler | Runtime |
| Runtime bundle | `runtime-bundle-v1` | `load_runtime_bundle()` | Graph runtime |

### Stage isolation (golden rule)

| Consumer | MAY read | MUST NOT read |
|----------|----------|---------------|
| **Runtime** | `graph.json`, `ProviderExecutionPlan.json`, `ExecutionRequest.json`, `RuntimeContext.json`, `manifest.json` | `ProductSpec`, `CreativeSpec`, `EditSpecs`, `cad.md`, `job-v1` |
| **CapabilityResolver** | `ProviderPrompt`, `ExecutionRequest`, provider registry | `cad.md`, `CreativeSpec` |
| **PromptCompiler** | All specs | Provider APIs |

---

## Compile directory layout

```
compile/
  manifest.json
  ProductSpec.json
  CreativeSpec.json
  EditSpecs.json
  ExecutionRequest.json
  ProviderPrompt.json
  ProviderExecutionPlan.json
  graph.json
  RuntimeContext.json
```

`manifest.json` lists every file with `content_hash`. Runtime loads via manifest pointers only.

---

## Roadmap

### U1 — Compiler produces artifact directory ✓

- `compile_bundle.write_compile_dir()`
- `compile_job.py --compile-dir <path>`
- Per-file SHA256 in manifest
- `lowering_version: job-v1@1`

**Exit:** Same `job-v1` compiled twice → identical manifest artifact hashes.

### U2 — Runtime consumes artifact directory ONLY ✓

- `compile_bundle.load_runtime_bundle()`
- `run_graph.py` / `run_job.py`: `job-v1` → compile → execute from manifest
- `graph_runtime` rejects spec languages in runtime ctx

**Exit:** Runtime loads `graph.json`, `ProviderExecutionPlan.json`, `ExecutionRequest.json`, `RuntimeContext.json` via manifest only.

### U3 — Golden replay tests ✓

- `tests/test_compile_bundle.py`, `tests/test_compile_replay.py`
- primikey scene-float-a: compile → dry-run → recompile hash match

### U4 — Freeze job-v1

- Schema const locked; lowering versioned in manifest
- Source language docs: legacy, not runtime input

### U5 — Language evolution (ProductSpec v2)

- Structured geometry — **not** a compiler blocker

### U6 — Edit DSL (EditSpec v2 ops)

### U7 — External provider registry

### U8 — Asset registry + content hashes

### U9 — Graph evolution (review gates, branches)

---

## Operator flow (post U2)

```bash
# Source → compile directory
python3 core/compile_job.py jobs/foo.job.json --compile-dir ./compile/foo

# Execute from compile directory (no recompile)
python3 core/run_graph.py ./compile/foo --dry-run

# Source → compile → execute (one shot)
python3 core/run_job.py jobs/foo.job.json --dry-run
```

`job-v1` remains forever as **legacy source language** — lowered at compile boundary, never executed directly.
