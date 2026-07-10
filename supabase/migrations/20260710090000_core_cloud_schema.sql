-- image-pipeline cloud replatform — canonical relational store
-- Brief #30201 (P1 of #30199 productization umbrella).
--
-- Target-state vocabulary (see docs/CLOUD_ARCHITECTURE.md):
--   workspaces  <- was: per-tenant disk folder (config/tenants.json row)
--   products    <- was: knowledge/<tenant>/products/*.json + jobs/<product>/ dir
--   campaigns   <- was: knowledge/<tenant>/campaigns/*.json + jobs/<campaign>/ folder
--   shots       <- was: jobs/**/<slug>.job.json  (one job-v1 JobSpec = one shot)
--   runs        <- was: workspace/<tenant>/runs registry rows + out/ render invocations
--   artifacts   <- was: workspace/<tenant>/artifact_registry.json rows + out/*.png files
--
-- This is the source-of-truth schema going forward. The legacy DDL under
-- db/migrations/ described the pre-replatform job-v1 layer; it is superseded
-- here by names that match the UI's own data model (product / shot / run /
-- artifact) so ui/server.py can read/write rows instead of tenant disk paths.

create extension if not exists pgcrypto;

-- ============================================================
-- 1. workspaces — a tenant. Was config/tenants.json + workspace/<slug>/.
-- ============================================================
create table if not exists workspaces (
  id              uuid primary key default gen_random_uuid(),
  slug            text not null unique,
  name            text not null,
  config          jsonb not null default '{}',   -- connection bundle, feature flags
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

-- ============================================================
-- 2. products — the thing being imaged. Reusable, editable.
--    Unifies knowledge-store ProductSpec-v2 and jobs_only (disk-scanned)
--    products behind one row; `spec` null == the old jobs_only case.
-- ============================================================
create table if not exists products (
  id              uuid primary key default gen_random_uuid(),
  workspace_id    uuid not null references workspaces(id) on delete cascade,
  slug            text not null,
  name            text not null,
  spec            jsonb,                         -- ProductSpec-v2 blob; null = jobs_only
  cad_md          text,                          -- cad.md contents (object truth)
  canonical_image_artifact_id uuid,             -- FK set after artifacts exist (see below)
  external_ref    jsonb not null default '{}',
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (workspace_id, slug)
);

-- ============================================================
-- 3. campaigns — optional grouping of products/shots.
-- ============================================================
create table if not exists campaigns (
  id              uuid primary key default gen_random_uuid(),
  workspace_id    uuid not null references workspaces(id) on delete cascade,
  slug            text not null,
  name            text not null,
  spec            jsonb not null default '{}',
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (workspace_id, slug)
);

-- ============================================================
-- 4. shots — one job-v1 JobSpec. The authoring unit ("+ New shot").
--    job_spec is the full job-v1 blob; a frozen copy travels onto each
--    run (runs.job_spec_snapshot) so a shot stays reproducible if edited.
-- ============================================================
create table if not exists shots (
  id              uuid primary key default gen_random_uuid(),
  workspace_id    uuid not null references workspaces(id) on delete cascade,
  product_id      uuid references products(id) on delete set null,
  campaign_id     uuid references campaigns(id) on delete set null,
  slug            text not null,                 -- e.g. "ankle-weights-fix-starz-branding"
  schema_version  text not null default 'job-v1',
  job_spec        jsonb not null default '{}',   -- the editable JobSpec
  prompt_template text,
  status          text not null default 'draft', -- draft|validated|ready|archived
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (workspace_id, slug)
);

-- ============================================================
-- 5. runs — ONE ROW PER EXECUTION. Was the runs registry + out/ invocation.
--    job_spec_snapshot is the immutable job-v1 JSON actually sent to engine.
-- ============================================================
create table if not exists runs (
  id                 uuid primary key default gen_random_uuid(),
  workspace_id       uuid not null references workspaces(id) on delete cascade,
  shot_id            uuid references shots(id) on delete set null,
  product_id         uuid references products(id) on delete set null,
  parent_run_id      uuid references runs(id) on delete set null,
  run_key            text,                       -- human/registry id
  status             text not null default 'queued', -- queued|compiling|generating|generated|failed
  dry_run            boolean not null default false,
  job_spec_snapshot  jsonb not null default '{}',
  engine             jsonb not null default '{}',
  cost_credits_estimated numeric,
  cost_credits_actual    numeric,
  external_job_ref   text,
  result             jsonb,
  error              text,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

-- ============================================================
-- 6. artifacts — rendered output images + review state.
--    storage_path points into Supabase Storage (bucket 'rendered-outputs');
--    the UI serves it via a signed URL, never a disk read.
-- ============================================================
create table if not exists artifacts (
  id              uuid primary key default gen_random_uuid(),
  workspace_id    uuid not null references workspaces(id) on delete cascade,
  product_id      uuid references products(id) on delete set null,
  shot_id         uuid references shots(id) on delete set null,
  run_id          uuid references runs(id) on delete set null,
  bucket          text not null default 'rendered-outputs',
  storage_path    text not null,                 -- object key within the bucket
  file_name       text not null,
  legacy_disk_path text,                         -- provenance for the importer only
  candidate_index int not null default 0,
  version         int not null default 1,
  status          text not null default 'pending' check (status in ('pending','approved','rejected')),
  comment         text,
  source          text,                          -- 'higgsfield_output' | 'drive' | 'upload' ...
  metadata        jsonb not null default '{}',
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (workspace_id, bucket, storage_path)
);

-- deferred FK: product canonical image -> an artifact
alter table products
  drop constraint if exists products_canonical_image_fk;
alter table products
  add constraint products_canonical_image_fk
  foreign key (canonical_image_artifact_id) references artifacts(id) on delete set null;

-- ============================================================
-- 7. artifact_comments — threaded, per-region review (Tibi's Drive comments).
-- ============================================================
create table if not exists artifact_comments (
  id                 uuid primary key default gen_random_uuid(),
  artifact_id        uuid not null references artifacts(id) on delete cascade,
  parent_comment_id  uuid references artifact_comments(id) on delete cascade,
  region             jsonb,                      -- bbox {x,y,w,h} or null = whole image
  body               text not null,
  author             text,
  created_at         timestamptz not null default now()
);

-- ---- indexes -------------------------------------------------
create index if not exists idx_products_workspace   on products(workspace_id);
create index if not exists idx_shots_workspace       on shots(workspace_id);
create index if not exists idx_shots_product         on shots(product_id);
create index if not exists idx_runs_workspace        on runs(workspace_id);
create index if not exists idx_runs_shot             on runs(shot_id);
create index if not exists idx_artifacts_workspace   on artifacts(workspace_id);
create index if not exists idx_artifacts_product     on artifacts(product_id);
create index if not exists idx_artifacts_status      on artifacts(workspace_id, status);
create index if not exists idx_comments_artifact     on artifact_comments(artifact_id);

-- ---- touch triggers -----------------------------------------
create or replace function set_updated_at() returns trigger as $$
begin new.updated_at = now(); return new; end;
$$ language plpgsql;

do $$
declare t text;
begin
  foreach t in array array['workspaces','products','campaigns','shots','runs','artifacts']
  loop
    execute format(
      'drop trigger if exists trg_%1$s_touch on %1$s; '
      'create trigger trg_%1$s_touch before update on %1$s '
      'for each row execute function set_updated_at();', t);
  end loop;
end $$;
