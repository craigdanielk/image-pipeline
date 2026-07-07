# image-pipeline — persistence schema

`db/migrations/20260703_create_image_pipeline_persistence_layer.sql` is the DDL — verified applying cleanly against a throwaway Postgres 15 instance (2026-07-03). This doc is the architecture rationale: why 6 tables not 1, and which node writes which column.

## Monolithic vs multi-table — the decision

**Multi-table, normalized authoring layer + JSONB snapshot on the run row.** Not a single flat table, and not a pure node-graph store either (that's Weavy's model — general-purpose visual canvas, overkill for our fixed 5-node linear pipeline). Reasoning:

- **CAD specs and reference images are reused across many jobs.** Ankle Weights alone will get re-run every time Tibi flags a branding tweak — 5 jobs so far this session, same CAD truth every time. A monolithic table duplicates that text/URL into every row: bloat, and worse, drift (edit the CAD description in job #4, jobs #1–3 now disagree with reality).
- **But a job must stay reproducible even if the source `cad_specs` row is edited later.** So `jobs.job_spec_snapshot` carries the *exact* job-v1 JSON blob that was actually sent to the engine — a frozen copy, independent of upstream edits. This is the same pattern as an orders table snapshotting product price/name at purchase time even though the product row can change afterward.
- **One row per run** (your explicit call) — `jobs` is exactly that. `output.count > 1` (batch candidates) doesn't violate this: the *job* is one row (one request/one prompt/one cost estimate), and each *resulting image* gets its own `job_outputs` row. That's the natural fix for "multiple images per product" too — many `job_outputs` rows trace back through many `jobs` rows to one `products` row.
- **Review needed per-region, per-image, threaded** — exactly what we just did manually with Tibi's Drive comments (bounding-box pins, replies). That's `job_output_comments`, one row per comment, not a text blob bolted onto the image row.

## Node → table/column map

The pipeline is `intake → validate → build_prompt → resolve_reference → engine(cost|generate) → persist`, plus the review loop you're running today (Drive comments) which the file-based pipeline has no home for at all — that's the actual gap this schema fixes.

| Node | Reads | Writes |
|---|---|---|
| **intake** (cli/http) | request payload | `jobs` row created: `product_id`, `desired_end_state`, `subject`, `output_requirements`, `engine` (requested), `status='draft'` |
| **validate** | `jobs.schema_version` + required fields | `jobs.status → 'validated'` or `jobs.error` set, stays `'draft'` |
| **build_prompt** | `cad_specs.content` (via `jobs.cad_spec_id`, or inline text) + `jobs.subject` + `output_requirements.composition/background` | `jobs.prompt_template`, `jobs.resolved_prompt` |
| **resolve_reference** | `reference_images.storage_url` (via `jobs.target_reference_id` + `jobs.product_reference_ids[]`) | `jobs.job_spec_snapshot.reference` / `.engine.params.input_images` (resolved URLs baked into the snapshot) |
| **engine — cost** | `jobs.job_spec_snapshot` | `jobs.cost_credits_estimated`, `status → 'cost_estimated'` |
| **engine — generate** | `jobs.job_spec_snapshot` | `jobs.external_job_ref`, `cost_credits_actual`, `status → 'generating' → 'generated'` |
| **persist** | engine response (N candidate images) | N rows in `job_outputs`: `storage_url`, `file_name` (`<id>__<imgtype>__v<N>.png`), `candidate_index`, `version` |
| **review** *(added — not in the original 5-node diagram, but this is where Tibi's comments actually live)* | `job_outputs` | `job_output_comments` rows (`region` bbox, `body`, `parent_comment_id` for replies); rolls up to `job_outputs.status` (approved/rejected) → `jobs.status → 'in_review'/'approved'/'rejected'` |

## Worked example — today's Ankle Weights branding-fix job

Maps 1:1 onto what we just hand-built as flat files:

- `products` row: Ankle Weights (Starz)
- `cad_specs` row: scope=`whole_product`, content = the physical description, source_refs = the 5 Shopify CDN URLs we pulled
- `reference_images` rows: one `role='target_anchor'` (the flawed kid render, `hf_..._143322...png`), one `role='product_ref'` (`S_Blue_Starz.png`)
- `jobs` row: `parent_job_id` = the original kid-hero generation that produced `143322`; `desired_end_state` = "swap plain JOGA wordmark for STARZ S-crest on the sock, nothing else moves"; `resolved_prompt` = the full prompt text we dry-ran; `job_spec_snapshot` = the exact JSON file we wrote; `status='cost_estimated'`, `cost_credits_estimated=2`
- `job_outputs`: not yet populated — job hasn't run for real yet
- `job_output_comments`: this is where Tibi's 5 screenshots would live if this had been built — `region` = the yellow bounding boxes from his Drive comments, `body` = "starz logo on equipment" etc., against the **prior** job's `job_outputs` row (the one this job's `parent_job_id` points back to)

That last point is the real payoff: **the review that triggered this fix job is queryable, not a screenshot in a chat.** `select body, region from job_output_comments where job_output_id = (select id from job_outputs where job_id = '<original-job>')` — instead of me eyeballing 18 images against 5 screenshots by hand, like this session.

## Graph runtime tables (2026-07-07)

Migration: `db/migrations/20260707_graph_runtime_persistence.sql` — **local only until applied to Supabase** (see `docs/RUNTIME.md`).

These tables sit alongside the legacy job-v1 layer; `run_graph.py` is the writer, `run_job.py` remains unchanged until explicitly bridged.

| Table | Purpose | Key columns |
|-------|---------|-------------|
| **compile_artifacts** | Content-addressed triple-spec compile cache | `tenant_id`, `content_hash` (unique together), `product_spec`, `creative_spec`, `execution_request`, `provider_prompt` |
| **graph_runs** | One row per graph-v1 execution | `graph_id`, `graph_snapshot`, `compile_hash`, `idempotency_key`, `parent_run_id`, `status`, `dry_run`, `result`, `error` |
| **graph_node_runs** | Per-node execution within a graph run | `graph_run_id` FK, `node_id`, `node_type`, `status`, `input_hash`, `output_snapshot` |

**Idempotency:** partial unique index on `(tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL`. Completed runs with matching key return cached `result` without re-execution.

**jobs bridge columns** (added to existing table):

| Column | Purpose |
|--------|---------|
| `compile_hash` | Links job row to compile cache entry |
| `provider_prompt` | Full ProviderPrompt v1 jsonb from `prompt.build` |
| `execution_request` | ExecutionRequest v1 snapshot |
| `graph_run_id` | FK to `graph_runs` when executed via `run_graph.py --track` |

## Design decisions (resolved 2026-07-03, Craig)

1. **No RLS.** `tenant_id` stays nullable, no row-level-security policies — single-operator today, revisit only if this becomes shared multi-tenant SaaS. Hard rule either way: **no keys, tokens, or secrets ever land in these tables** — engine params, storage locations, everything here is either public-facing or internal-plan data, never credentials.
2. **No engine adapter registry.** Higgsfield is the only adapter in use — `jobs.engine` stays freeform JSONB, no `engines` lookup table. Revisit if/when a second adapter (fal/replicate/openai) actually gets built.
3. **Storage is dual: remote + local disk, same column, array-shaped.** Every image-bearing row (`reference_images`, `job_outputs`) now carries `storage_locations jsonb` — an array of `{kind, location}` pairs instead of a single `storage_url`. A row typically holds both a `{"kind":"url", "location":"https://cdn..."}` entry (the durable/shareable copy) and a `{"kind":"local_path", "location":"/Users/.../out/x.png"}` entry (where it actually sits on disk when materialized, e.g. under `jobs/<product>/out/`). Agnostic to how many locations a given asset has — add `{"kind":"s3", ...}` later without a schema change.
