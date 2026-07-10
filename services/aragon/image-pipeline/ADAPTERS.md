# Adapter Contracts (Frozen Baseline)

**Status:** FROZEN — do not change function signatures without a schema version bump.  
**Baseline:** see `docs/GRAPH_MIGRATION_BASELINE.md` for the git SHA this contract was captured against.  
**Purpose:** These four seams are the **only** places the pure core (`core/run_job.py`) touches the outside world. The graph-v1 execution engine MUST call the same adapter interfaces; provider swaps are registry entries, not core changes.

## Registry (`core/adapters/__init__.py`)

| Seam | Default ID | Module | Resolver |
|------|------------|--------|----------|
| intake | `cli` | `intake_cli` | `get_intake(name="cli")` |
| reference | `local` | `reference_local` | `get_reference(name="local")` |
| engine | *(from JobSpec `engine.name`)* | `engine_higgsfield` | `get_engine(name)` |
| output | `local` | `output_local` | `get_output(name="local")` |

Unknown adapter names raise `KeyError` with the list of available IDs.

---

## 1. Intake — `intake_cli.load`

**Module:** `core/adapters/intake_cli.py`

```python
def load(source: str) -> dict:
    """Load and validate a JobSpec.

    Args:
        source: Path to a `.job.json` file, or `'-'` to read JSON from stdin.

    Returns:
        Validated JobSpec dict with `_dir` set to the job file's directory
        (or `'.'` for stdin).

    Raises:
        jobspec.JobSpecError: Malformed JobSpec.
        json.JSONDecodeError: Invalid JSON.
        OSError: File not found.
    """
```

**Contract invariants:**
- Always calls `jobspec.validate(job)` before return.
- Sets `job["_dir"]` for relative path resolution downstream.

**SaaS swap:** `http` intake returns the same dict shape from a POST body.

---

## 2. Reference — `reference_local.resolve`

**Module:** `core/adapters/reference_local.py`

```python
def resolve(reference, job_dir: str = ".") -> str | None:
    """Resolve the look-anchor to a local filesystem path.

    Args:
        reference: One of:
            - `None` — no reference (text-to-image)
            - `str` — local path (relative to job_dir) or http(s) URL
            - `dict` — `{"image": str, "optional": bool}` (default optional=False)
        job_dir: Base directory for relative paths.

    Returns:
        Absolute or temp local path, or `None` when reference is absent/optional-miss.

    Raises:
        FileNotFoundError: Required reference path does not exist.
    """
```

**Contract invariants:**
- HTTP(S) URLs are downloaded to a temp file (`ip-ref-*` prefix).
- Query strings stripped before suffix extraction (canonical behaviour).
- Optional references log a skip and return `None` instead of raising.

**Consumer extension (compile-time — optional per deployment):**
- Some consumers pass `reference` as a **list** of str/dict items; graph compiler lowers
  this to `image_path_list` ports. Canonical adapter signatures above remain the runtime floor.

**SaaS swap:** `s3` / `drive` adapters return the same local temp path shape.

---

## 3. Engine — `engine_higgsfield.cost` / `engine_higgsfield.generate`

**Module:** `core/adapters/engine_higgsfield.py`

```python
def cost(
    prompt: str,
    output: dict,
    engine: dict,
    job_dir: str = ".",
) -> dict:
    """Dry-run cost estimate. No credits, no uploads, no files.

    Args:
        prompt: Fully resolved prompt string.
        output: JobSpec `output` block (aspect_ratio, resolution, count, imgtype, dir, …).
        engine: JobSpec `engine` block (name, model, model_type, params, …).
        job_dir: Base for resolving `engine.params.input_images` paths.

    Returns:
        Parsed JSON from `higgsfield generate cost … --json`.
    """


def generate(
    prompt: str,
    reference: str | None,
    output: dict,
    engine: dict,
    job_dir: str = ".",
) -> list[dict]:
    """Run generation and block until complete.

    Args:
        prompt: Fully resolved prompt string.
        reference: Local path from reference adapter, or None for text-to-image.
        output: JobSpec `output` block.
        engine: JobSpec `engine` block.
        job_dir: Base for resolving `engine.params.input_images` paths.

    Returns:
        List of completed job dicts, each with at minimum:
        `{"status": "completed", "result_url": str, "id": str, …}`.

    Raises:
        RuntimeError: CLI failure or generation not completed.
        FileNotFoundError: input_images path missing.
    """
```

**Contract invariants:**
- `--image` passed **only** when `reference` is truthy (text-only models reject it).
- `engine.params` keys become `--<key> <value>` CLI flags (escape hatch).
- `output.count` > 1 maps to `--batch_size`.
- `input_images` local paths are uploaded via `higgsfield upload create`; HTTP URLs in canonical are downloaded then uploaded (see baseline doc for consumer URL-object variants).
- Subprocess: `higgsfield` must be on `PATH`.

**Consumer extension (compile-time):**
- `reference` may be `list[str]` → multiple `--image` flags (canonical since 2026-07-07).

**SaaS swap:** `fal` / `replicate` adapters expose identical `cost` / `generate` signatures and return the same normalized result list.

---

## 4. Output — `output_local.persist`

**Module:** `core/adapters/output_local.py`

```python
def persist(results: list[dict], job: dict, out_dir: str) -> list[str]:
    """Download engine results to versioned local PNG files.

    Args:
        results: Engine result list (`result_url`, `id`, …).
        job: Full JobSpec dict (uses `job['id']` and `job['output']['imgtype']`).
        out_dir: Directory for saved files (created if missing).

    Returns:
        List of saved absolute paths:
        `<out_dir>/<id>__<imgtype>__v<N>.png` (N auto-increments, never overwrites).
    """
```

**Contract invariants:**
- Version suffix `__vN` globbed to pick next N.
- Default `imgtype` is `"hero"` when omitted.

**SaaS swap:** `s3` / `webhook` adapters receive the same `results` list.

---

## Core orchestration (`core/run_job.py`)

The runner calls adapters in this **fixed order** (graph-v1 linear compile target):

```
intake.load(path)
  → jobspec.validate(job)
  → build_prompt(job)          # core function, not an adapter
  → reference.resolve(ref, job_dir)
  → engine.cost | engine.generate(prompt, ref, output, engine, job_dir)
  → output.persist(results, job, out_dir)
```

Optional `tracking.py` mirrors stages to Supabase when `--track` is set; tracking is **not** a fifth adapter seam.

---

## Change policy

| Change type | Allowed under freeze? |
|-------------|---------------------|
| Doc-only (this file) | Yes |
| Adapter **signature** change | **No** — requires `job-v2` or graph node version bump |
| New optional adapter ID in registry | Yes (additive) |
| Engine CLI flag mapping inside adapter | Yes if signature unchanged |
| Core `run_job.py` sequence reorder | **No** — graph compiler depends on this order |

Graph-v1 node handlers MUST delegate to these adapter functions — not reimplement provider logic.
