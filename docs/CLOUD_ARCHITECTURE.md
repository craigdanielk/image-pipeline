# Cloud Architecture — image-pipeline replatform

**Brief #30201** (P1 of the #30199 productization umbrella).

## End state

No image-pipeline operator state lives on local disk. Specs and registry rows
are Postgres rows; media (reference images + rendered outputs) is Supabase
Storage; the API (`ui/server.py`) is location-independent — a fresh checkout
with only `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` serves the full UI
(products, shots, assets, runs) with **zero reads from any tenant disk path**.

The core compiler (`core/`) stays disk/dep-free and unchanged. This replatform
is entirely in the operator layer (`ui/`, `supabase/`, `tools/`).

## What replaced each disk path

| Old (disk) | New (cloud) | Where |
|---|---|---|
| `config/tenants.json` row, `workspace/<slug>/` | `workspaces` table | `supabase/migrations/…_core_cloud_schema.sql` |
| `knowledge/<tenant>/products/*.json`, `jobs/<product>/` folder | `products` table (`spec` jsonb, `cad_md` text; `spec` null = jobs_only) | same |
| `knowledge/<tenant>/campaigns/*.json` | `campaigns` table | same |
| `jobs/**/*.job.json` (a job-v1 JobSpec) | `shots` table (`job_spec` jsonb) | same |
| `workspace/<tenant>/` runs registry rows | `runs` table (`job_spec_snapshot` immutable) | same |
| `workspace/<tenant>/artifact_registry.json` rows | `artifacts` table + `artifact_comments` | same |
| `jobs/**/reference/`, `jobs/**/refs/` image files | Storage bucket `reference-images` | `…_storage_buckets.sql` |
| `jobs/**/out/*.png\|webp` render files | Storage bucket `rendered-outputs` | same |
| `GET /media?path=<disk>` → `FileResponse` | `GET /media?path=<object-key>` → **307 redirect to signed URL** | `ui/server.py`, `ui/cloud_store.signed_url` |

## Components

### 1. Schema — `supabase/migrations/`
Source-of-truth relational store. Six authoring/execution tables +
`artifact_comments`. Design principles carried from the legacy `db/SCHEMA.md`:

- **Normalized authoring + immutable snapshot.** `shots.job_spec` is editable;
  each `runs.job_spec_snapshot` is a frozen copy of exactly what was sent to the
  engine, so a run stays reproducible even if the shot is edited later (orders
  snapshot price/name at purchase time — same pattern).
- **One row per run.** Batch candidates fan out to multiple `artifacts` rows,
  each tracing `artifact → run → shot → product`.
- **Threaded, per-region review.** `artifact_comments` (bbox `region`,
  `parent_comment_id`) makes Tibi's Drive-comment review queryable, not a
  screenshot in a chat.
- **No RLS today** (single-operator; service key). Multi-tenant SaaS would add
  per-workspace policies keyed on the object-path prefix. **No credentials ever
  land in these tables.**

### 2. Storage — private buckets + signed URLs
`reference-images` and `rendered-outputs` are **private**. The UI never exposes
a raw object URL or a disk path: `cloud_store.signed_url(bucket, key)` mints a
short-lived signed URL (TTL `IMAGE_PIPELINE_SIGNED_URL_TTL`, default 3600s) per
request. `GET /media?path=` 307-redirects to it. `list_artifacts` returns rows
pre-signed, so gallery images render straight from Storage.

### 3. Data access — `ui/cloud_store.py`
Dep-free Supabase client (urllib only, matching the core's zero-dependency
rule). Talks to two REST surfaces:

- **PostgREST** (`/rest/v1/<table>`) — `select`/`insert`(upsert)/`update`/
  `delete`, plus domain helpers (`list_artifacts`, `set_artifact_status`,
  `upsert_product`, `create_run`, `upsert_shot`, `add_comment`, …).
- **Storage** (`/storage/v1/object/…`) — `signed_url`, `upload_object`.

`cloud_store.enabled()` returns True iff `SUPABASE_URL` + a service key are set.

### 4. API — `ui/server.py`
Every replatformed endpoint branches cloud-first when `cloud_store.enabled()`,
falling back to the legacy disk path otherwise (additive, safe to ship before
provisioning). Cloud-backed today:

- `GET /api/products`, `GET /api/products/{slug}` → `products` table
- `GET /api/artifacts` → `artifacts` rows with signed URLs
- `POST /api/artifacts/{id}/approve`, `DELETE /api/artifacts/{id}` (reject),
  `PATCH /api/artifacts/{id}` (comment), `.../restore` → Postgres round-trip
  + `artifact_comments`
- `GET /media` → signed Storage URL redirect

### 5. Importer — `tools/import_joga_workspace.py`
One-shot, idempotent migration of a tenant disk workspace into the store:
products (+ `cad.md`), shots (JobSpecs), artifacts (media uploaded to
`rendered-outputs`, review status/comment carried from the disk registry).
`--dry-run` rehearses; a dry-run against the real joga `jobs_root` resolves
11 products, 253 shots, 28 artifacts.

```bash
export SUPABASE_URL=…  SUPABASE_SERVICE_ROLE_KEY=…
python tools/import_joga_workspace.py --tenant joga --dry-run   # rehearse
python tools/import_joga_workspace.py --tenant joga             # commit
```

## Deploy / bootstrap

```bash
# 1. apply schema + buckets
supabase db push            # or psql -f supabase/migrations/*.sql

# 2. configure the API (only these two vars needed)
export SUPABASE_URL=https://<ref>.supabase.co
export SUPABASE_SERVICE_ROLE_KEY=<service-role-key>

# 3. seed data
python tools/import_joga_workspace.py --tenant joga

# 4. run — no tenant disk mount required
python ui/server.py
```

## Acceptance mapping

| Acceptance criterion | Satisfied by |
|---|---|
| Fresh checkout w/ only Supabase env serves full UI, no tenant disk reads | `cloud_store.enabled()` gates every endpoint; no disk fallback triggers when env set |
| Artifact approve/reject/comment round-trips through Postgres | `set_artifact_status` / `add_comment` on `artifacts` + `artifact_comments` |
| Images render via signed Storage URLs | `signed_url` + `/media` 307 redirect + pre-signed `list_artifacts` |
| joga workspace visible after importer | `tools/import_joga_workspace.py` |

## Not yet migrated (follow-on)

Knowledge campaign CRUD, graph-run streaming, and the `/generate` legacy page
still read disk in places (they fall back cleanly). They are tracked as the next
slice of the #30199 umbrella; the store layer (`cloud_store.py`) already exposes
the primitives (`create_run`, `upsert_shot`, campaign tables) they need.
