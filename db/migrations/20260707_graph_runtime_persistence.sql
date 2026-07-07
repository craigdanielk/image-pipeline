-- Graph runtime persistence — compile cache, graph runs, node runs.
-- Design doc: docs/RUNTIME.md + db/SCHEMA.md (graph tables section).
-- Local-only until applied to Supabase via `supabase db push`.

create extension if not exists pgcrypto;

-- ============================================================
-- compile_artifacts — idempotent triple-spec compile cache
-- ============================================================
create table if not exists compile_artifacts (
  id                  uuid primary key default gen_random_uuid(),
  tenant_id           uuid,                         -- null = tenant-agnostic
  content_hash        text not null,
  product_spec        jsonb not null,
  creative_spec       jsonb not null,
  execution_request   jsonb not null,
  provider_prompt     jsonb not null,
  created_at          timestamptz not null default now(),
  unique (tenant_id, content_hash)
);

create index if not exists idx_compile_artifacts_hash
  on compile_artifacts (content_hash);

-- ============================================================
-- graph_runs — one row per graph execution (re-run safe)
-- ============================================================
create table if not exists graph_runs (
  id                  uuid primary key default gen_random_uuid(),
  tenant_id           uuid,
  graph_id            text not null,
  graph_snapshot      jsonb not null,
  compile_hash        text,
  idempotency_key     text,
  parent_run_id       uuid references graph_runs(id) on delete set null,
  status              text not null default 'running'
                        check (status in ('running', 'completed', 'failed')),
  dry_run             boolean not null default false,
  result              jsonb,
  error               text,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

create unique index if not exists idx_graph_runs_idempotency
  on graph_runs (tenant_id, idempotency_key)
  where idempotency_key is not null;

create index if not exists idx_graph_runs_graph_id
  on graph_runs (graph_id);

create index if not exists idx_graph_runs_status
  on graph_runs (status);

-- ============================================================
-- graph_node_runs — per-node execution record within a graph run
-- ============================================================
create table if not exists graph_node_runs (
  id                  uuid primary key default gen_random_uuid(),
  graph_run_id        uuid not null references graph_runs(id) on delete cascade,
  node_id             text not null,
  node_type           text not null,
  status              text not null
                        check (status in ('running', 'completed', 'failed', 'skipped')),
  input_hash          text,
  output_snapshot     jsonb,
  error               text,
  created_at          timestamptz not null default now(),
  unique (graph_run_id, node_id)
);

create index if not exists idx_graph_node_runs_graph_run
  on graph_node_runs (graph_run_id);

-- ============================================================
-- Bridge legacy jobs table → graph runtime
-- ============================================================
alter table jobs add column if not exists compile_hash text;
alter table jobs add column if not exists provider_prompt jsonb;
alter table jobs add column if not exists execution_request jsonb;
alter table jobs add column if not exists graph_run_id uuid
  references graph_runs(id) on delete set null;

create index if not exists idx_jobs_graph_run
  on jobs (graph_run_id);

create index if not exists idx_jobs_compile_hash
  on jobs (compile_hash);
