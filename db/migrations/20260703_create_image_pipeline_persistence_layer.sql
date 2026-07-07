-- image-pipeline persistence layer — job-v1 schema, Postgres/Supabase
-- Design doc: db/SCHEMA.md — read that first for the node-to-table mapping and rationale.

create extension if not exists pgcrypto;

-- ============================================================
-- 1. products — the thing being imaged. Reusable, editable.
-- ============================================================
create table products (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       uuid,                         -- null = single-tenant / agnostic use
  name            text not null,
  slug            text not null,
  external_ref    jsonb not null default '{}',  -- e.g. {"shopify_gid": "...", "url": "..."}
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (tenant_id, slug)
);

-- ============================================================
-- 2. cad_specs — the extracted "object truth". Reusable, versioned.
--    Covers both "whole product" and "specific element" extraction.
-- ============================================================
create table cad_specs (
  id              uuid primary key default gen_random_uuid(),
  product_id      uuid references products(id) on delete cascade,
  scope           text not null check (scope in ('whole_product', 'element')),
  element_name    text,                          -- required when scope = 'element'
  source_type     text not null check (source_type in ('engineering_cad', 'visual_extraction', 'manual_description')),
  content         text not null,                 -- the CAD/description text — folds into {cad} at build_prompt time
  source_refs     jsonb not null default '[]',   -- array of URLs/paths this was derived from
  version         int not null default 1,
  created_by      text,
  created_at      timestamptz not null default now(),
  check (scope <> 'element' or element_name is not null)
);

-- ============================================================
-- 3. reference_images — asset library for INPUT-side images
--    (target anchors, product refs, style refs). Outputs live in job_outputs.
-- ============================================================
create table reference_images (
  id              uuid primary key default gen_random_uuid(),
  product_id      uuid references products(id) on delete set null,
  role            text not null check (role in ('target_anchor', 'product_ref', 'style_ref')),
  storage_locations jsonb not null default '[]',  -- array of {kind, location} — e.g. [{"kind":"url","location":"https://cdn..."},{"kind":"local_path","location":"/Users/.../out/x.png"}]
  source          text,                          -- 'shopify_cdn' | 'upload' | 'drive' | 'higgsfield_output' ...
  metadata        jsonb not null default '{}',    -- {width, height, alt, notes}
  created_at      timestamptz not null default now(),
  check (jsonb_typeof(storage_locations) = 'array' and jsonb_array_length(storage_locations) > 0)
);

-- ============================================================
-- 4. jobs — ONE ROW PER RUN. The core table.
--    Normalized FKs for reuse/authoring + a full JSONB snapshot for
--    immutable replay (job-v1 contract), so a job stays reproducible
--    even if the source cad_spec/reference_image rows change later.
-- ============================================================
create table jobs (
  id                       uuid primary key default gen_random_uuid(),
  tenant_id                uuid,
  product_id               uuid references products(id) on delete set null,
  parent_job_id            uuid references jobs(id) on delete set null,  -- edit/regen lineage

  schema_version           text not null default 'job-v1',
  external_slug            text not null,          -- human id, e.g. "ankle-weights-fix-starz-branding"

  cad_spec_id              uuid references cad_specs(id) on delete set null,
  target_reference_id      uuid references reference_images(id) on delete set null,
  product_reference_ids    uuid[] not null default '{}',  -- multi-ref input_images

  desired_end_state        text not null,          -- freeform human brief — the "A stage" input
  subject                  jsonb not null default '{}',   -- {placeholder: value} map
  output_requirements      jsonb not null default '{}',   -- {imgtype, aspect_ratio, resolution, count, background, composition, colors}

  prompt_template          text not null,          -- templated, pre-resolution
  resolved_prompt          text,                    -- final prompt sent to engine (build_prompt node output)
  negative_prompt          text,

  engine                   jsonb not null,          -- {name, model, model_type, params}
  job_spec_snapshot        jsonb not null,          -- full job-v1 JSON blob, exact replay copy

  status                   text not null default 'draft' check (status in (
                              'draft', 'validated', 'cost_estimated', 'queued',
                              'generating', 'generated', 'in_review',
                              'approved', 'rejected', 'failed'
                            )),
  cost_credits_estimated   numeric,
  cost_credits_actual      numeric,
  external_job_ref         text,                    -- Higgsfield's own job/generation id
  error                    text,

  requested_by             text,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);

create index idx_jobs_product on jobs(product_id);
create index idx_jobs_status on jobs(status);
create index idx_jobs_parent on jobs(parent_job_id);

-- ============================================================
-- 5. job_outputs — ONE ROW PER GENERATED IMAGE.
--    Handles output.count > 1 (batch candidates) and multiple
--    images per product naturally — many rows, one job_id each.
-- ============================================================
create table job_outputs (
  id               uuid primary key default gen_random_uuid(),
  job_id           uuid not null references jobs(id) on delete cascade,
  candidate_index  int not null default 0,         -- position within the batch (count)
  storage_locations jsonb not null default '[]',   -- array of {kind, location} — remote URL + local disk path both live here
  file_name        text,                            -- <id>__<imgtype>__v<N>.png convention
  width            int,
  height           int,
  version          int not null default 1,
  status           text not null default 'pending' check (status in (
                      'pending', 'generated', 'approved', 'rejected', 'superseded'
                    )),
  created_at       timestamptz not null default now(),
  unique (job_id, candidate_index)
);

create index idx_job_outputs_job on job_outputs(job_id);

-- ============================================================
-- 6. job_output_comments — the review layer. Mirrors per-region
--    Drive-style comments (bounding box + threaded replies).
-- ============================================================
create table job_output_comments (
  id                 uuid primary key default gen_random_uuid(),
  job_output_id      uuid not null references job_outputs(id) on delete cascade,
  author             text not null,
  body               text not null,
  region             jsonb,                         -- {x, y, w, h} normalized 0-1, null = whole-image comment
  parent_comment_id  uuid references job_output_comments(id) on delete cascade,
  resolved           boolean not null default false,
  created_at         timestamptz not null default now()
);

create index idx_comments_output on job_output_comments(job_output_id);
