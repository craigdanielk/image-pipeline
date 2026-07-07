# Creative Compiler (`image-pipeline`)

Open-source **deterministic creative compiler** with provider-agnostic backends.

> **The runtime never compiles. The compiler never executes.**

Source languages (`job-v1`, `cad.md`) lower at the compile boundary into inspectable
`compile/` artifact directories. The graph runtime executes from `manifest.json` only.

| Phase | Does | Must not |
|-------|------|----------|
| **Compile** | Specs → `ProviderPrompt` → `ProviderExecutionPlan` → `graph.json` | Call providers |
| **Execute** | Graph IR + plan → adapters → images | Read `cad.md` or lower `job-v1` |

**Docs:** [`docs/CREATIVE_COMPILER_LANGUAGE_SPEC.md`](./docs/CREATIVE_COMPILER_LANGUAGE_SPEC.md) · [`docs/COMPILER_BUILD_PLAN.md`](./docs/COMPILER_BUILD_PLAN.md)

---

## Quickstart (CLI)

```bash
git clone https://github.com/craigdanielk/image-pipeline.git
cd image-pipeline

# Cost estimate (no credits)
python3 core/run_job.py jobs/_example.job.json --dry-run

# Compile only — inspect per-stage artifacts
python3 core/compile_job.py jobs/_example.job.json --compile-dir ./compile/example

# Execute from compile/ directory
python3 core/run_graph.py ./compile/example --dry-run
```

**Requires:** Python 3.9+ (core is dep-free) and the [Higgsfield CLI](https://www.npmjs.com/package/higgsfield) on `PATH` for the default engine.

---

## Operator UI (compile + view outputs)

Local web UI to compile jobs, dry-run, generate, and **view output PNGs**.

```bash
pip install -r ui/requirements.txt
python ui/server.py
# → http://127.0.0.1:8787
```

1. Select a job under `jobs/`
2. **Compile** — writes `jobs/<product>/compile/<job-id>/`
3. **Dry run** — cost estimate
4. **Generate** — calls engine (spends credits if configured)
5. **Outputs** — gallery of `out/*.png` for that job

See [`ui/README.md`](./ui/README.md).

---

## Compile directory contract

```
compile/<job-id>/
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

Same `job-v1` compiled twice → identical per-file hashes in `manifest.json`.

---

## Consumer workspaces (tenant-agnostic)

Brand-specific job libraries live **outside** this repo, e.g.:

```
tenants/<slug>/tools/image-pipeline/jobs/...
```

Run consumer jobs through this engine:

```bash
python3 core/run_job.py /path/to/tenants/joga/tools/image-pipeline/jobs/foo.job.json --dry-run
```

---

## Repository layout

| Path | Role |
|------|------|
| `core/` | Compiler, graph runtime, adapters (dep-free Python) |
| `schemas/` | Normative JSON schemas |
| `knowledge/` | Structured operation/product libraries (v2 path) |
| `jobs/` | Example job-v1 sources |
| `ui/` | Local operator UI (FastAPI — separate requirements) |
| `pipeline/` | Legacy Regenic Sheets pipeline (archived reference) |
| `tests/` | 64+ unit tests |

---

## Development

```bash
python3 -m pytest tests/ -q
```

---

## License

MIT — see [`LICENSE`](./LICENSE).

Extracted from the ARAGON monorepo (`services/aragon/image-pipeline`) as a dedicated service repository.
