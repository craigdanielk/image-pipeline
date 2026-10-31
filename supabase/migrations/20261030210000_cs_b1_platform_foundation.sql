-- =============================================================================
-- CS-B1 / NS-10803 — Creative Studio hosted platform foundation
--
-- Implements the CS-B1 subset of docs/creative-studio/platform/DATABASE_CONTRACT.md
-- and database-contract.json:
--   * restricted runtime roles cs_api / cs_worker (no superuser, no BYPASSRLS,
--     no DDL, no table ownership, no inherited roles);
--   * authenticated workspace membership (accounts, workspaces, members) that
--     replaces public tenant slugs / disk-root trust;
--   * durable records: runs (jobs), run nodes, run events, work items,
--     stage results (receipts), assets, immutable asset versions, idempotency
--     receipts and audit events;
--   * private storage: one private `creative-studio` bucket, exact-key upload
--     intents and <=60 s download grants checked by storage.objects RLS;
--   * scheduling primitive for CS-B3: lease/fence claim, heartbeat, fenced
--     finish and bounded reschedule over cs_work_items.
--
-- Out of scope here (later tasks, nothing is faked for them): plans/recipes and
-- operations (CS-B2), admission/budgets/attempts/provider receipts/pgmq and the
-- pg_cron dispatcher (CS-B3), exports/compositions/handoffs (CS-B4),
-- runtime login credentials and deployment (CS-B5).
--
-- Re-runnable: every object is created IF NOT EXISTS / OR REPLACE, B1's own
-- policies are dropped and re-created, the bucket insert is ON CONFLICT DO NOTHING.
-- Re-running this file on a database that already carries later CS migrations
-- touches only B1's own policies and function privileges, and never replaces a
-- function body a later migration has redefined (section 0b / 16).
-- No project-wide default privileges are changed.
-- =============================================================================

create extension if not exists pgcrypto;

-- -----------------------------------------------------------------------------
-- 0. Preconditions. SECURITY DEFINER helpers below read FORCE-RLS membership
--    tables; their owner (the migration owner) must bypass RLS. Supabase's
--    `postgres` role is BYPASSRLS. Refuse to install otherwise rather than ship
--    helpers that silently deny (or, worse, are later "fixed" with broad grants).
-- -----------------------------------------------------------------------------
do $$
begin
  if not exists (select 1 from pg_roles where rolname = current_user and (rolsuper or rolbypassrls)) then
    raise exception 'cs_b1: migration owner % must be superuser or BYPASSRLS', current_user;
  end if;
  if to_regrole('authenticated') is null or to_regrole('anon') is null then
    raise exception 'cs_b1: Supabase roles authenticated/anon are required';
  end if;
  if to_regclass('auth.users') is null or to_regclass('storage.objects') is null
     or to_regclass('storage.buckets') is null then
    raise exception 'cs_b1: auth.users and storage.objects/buckets are required';
  end if;
end $$;

-- -----------------------------------------------------------------------------
-- 0b. Re-run guard (CS-INT F1). Later CS migrations (e.g. CS-B2) redefine some
--     B1 functions (cs_claim_work, cs_finish_work, cs_reschedule_work,
--     cs_extension_for_mime). Re-running this file must not revert them: every
--     cs_* function body that already exists is snapshotted here and restored in
--     section 16 if this file replaced it. On a fresh database the snapshot is
--     empty and nothing is restored.
-- -----------------------------------------------------------------------------
drop table if exists pg_temp.cs_b1_function_snapshot;
create temp table cs_b1_function_snapshot as
  select p.oid as fn_oid, pg_get_functiondef(p.oid) as fn_def
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
   where p.prokind = 'f'
     and ((n.nspname = 'public' and p.proname like 'cs\_%') or n.nspname = 'cs_private');

-- -----------------------------------------------------------------------------
-- 1. Runtime roles. NOLOGIN here: login credentials are provisioned at
--    deployment (CS-B5), never committed. Attributes are asserted after creation.
-- -----------------------------------------------------------------------------
do $$
begin
  if to_regrole('cs_api') is null then
    create role cs_api nologin noinherit nosuperuser nocreatedb nocreaterole nobypassrls noreplication;
  end if;
  if to_regrole('cs_worker') is null then
    create role cs_worker nologin noinherit nosuperuser nocreatedb nocreaterole nobypassrls noreplication;
  end if;
  if exists (select 1 from pg_roles where rolname in ('cs_api', 'cs_worker')
             and (rolsuper or rolbypassrls or rolcreaterole or rolcreatedb or rolreplication)) then
    raise exception 'cs_b1: cs_api/cs_worker carry a forbidden attribute';
  end if;
  if exists (select 1 from pg_auth_members am join pg_roles r on r.oid = am.member
             where r.rolname in ('cs_api', 'cs_worker')) then
    raise exception 'cs_b1: cs_api/cs_worker must not be members of other roles';
  end if;
end $$;

create schema if not exists cs_private;
revoke all on schema cs_private from public, anon;
grant usage on schema cs_private to cs_api, cs_worker, authenticated;
grant usage on schema public to cs_api, cs_worker;

-- -----------------------------------------------------------------------------
-- 2. Enumerations (values verbatim from database-contract.json "enums").
-- -----------------------------------------------------------------------------
do $$
begin
  if to_regtype('public.cs_workspace_role') is null then
    create type public.cs_workspace_role as enum ('producer', 'reviewer', 'viewer');
  end if;
  if to_regtype('public.cs_run_state') is null then
    create type public.cs_run_state as enum ('queued', 'running', 'waiting_provider', 'needs_reconciliation',
      'budget_blocked', 'authorization_blocked', 'cancel_requested', 'completed', 'failed', 'cancelled');
  end if;
  if to_regtype('public.cs_node_state') is null then
    create type public.cs_node_state as enum ('pending', 'queued', 'running', 'waiting_provider',
      'needs_reconciliation', 'budget_blocked', 'authorization_blocked', 'succeeded', 'failed', 'cancelled', 'skipped');
  end if;
  if to_regtype('public.cs_cost_state') is null then
    create type public.cs_cost_state as enum ('reserved', 'settled', 'unknown');
  end if;
  if to_regtype('public.cs_work_state') is null then
    create type public.cs_work_state as enum ('queued', 'leased', 'retry_wait', 'done', 'dead', 'cancelled');
  end if;
  if to_regtype('public.cs_work_stage') is null then
    create type public.cs_work_stage as enum ('compile', 'prepare_reference', 'provider_submit',
      'provider_reconcile_submission', 'provider_poll', 'provider_cancel', 'ingest_original', 'compose_image',
      'normalize_video_segment', 'render_video_transition', 'mux_video', 'render_thumbnail', 'publish_export',
      'deliver_handoff', 'import_brand_package', 'author_treatment', 'persist_treatment_draft');
  end if;
end $$;

-- -----------------------------------------------------------------------------
-- 3. Private service tables
-- -----------------------------------------------------------------------------
create table if not exists cs_private.cs_runtime_limits (
  environment_id        text primary key check (environment_id ~ '^[a-z0-9][a-z0-9_-]{0,39}$'),
  admission_enabled     boolean not null default false,
  dispatch_enabled      boolean not null default false,
  paid_provider_enabled boolean not null default false,
  heavy_slots           integer not null default 1 check (heavy_slots between 0 and 8),
  active_run_limit      integer not null default 2 check (active_run_limit >= 0),
  max_dispatch_batch    integer not null default 2 check (max_dispatch_batch between 1 and 16),
  upload_intent_seconds integer not null default 600 check (upload_intent_seconds between 60 and 3600),
  revision              integer not null default 1 check (revision > 0),
  updated_at            timestamptz not null default now()
);

create table if not exists cs_private.cs_runtime_principals (
  user_id        uuid not null references auth.users(id),
  environment_id text not null references cs_private.cs_runtime_limits(environment_id),
  role           text not null default 'storage_worker' check (role = 'storage_worker'),
  status         text not null default 'active' check (status in ('active', 'revoked')),
  created_at     timestamptz not null default now(),
  primary key (user_id, environment_id)
);

-- -----------------------------------------------------------------------------
-- 4. Accounts, workspaces, membership
-- -----------------------------------------------------------------------------
create table if not exists public.cs_accounts (
  id         uuid primary key default gen_random_uuid(),
  owner_id   uuid not null references auth.users(id),
  name       text not null check (length(btrim(name)) between 1 and 200),
  status     text not null default 'active' check (status in ('active', 'suspended', 'closed')),
  created_at timestamptz not null default now()
);

create table if not exists public.cs_account_members (
  account_id uuid not null references public.cs_accounts(id),
  user_id    uuid not null references auth.users(id),
  role       text not null check (role in ('owner', 'admin')),
  created_at timestamptz not null default now(),
  revoked_at timestamptz,
  primary key (account_id, user_id)
);

create table if not exists public.cs_workspaces (
  id              uuid primary key default gen_random_uuid(),
  account_id      uuid not null references public.cs_accounts(id),
  name            text not null check (length(btrim(name)) between 1 and 200),
  mode            text not null default 'native' check (mode in ('native', 'external')),
  status          text not null default 'active' check (status in ('active', 'suspended', 'closed')),
  policy_revision integer not null default 1 check (policy_revision > 0),
  created_by      uuid references auth.users(id),
  created_at      timestamptz not null default now(),
  unique (account_id, id)
);

create table if not exists public.cs_workspace_members (
  workspace_id uuid not null references public.cs_workspaces(id),
  user_id      uuid not null references auth.users(id),
  role         public.cs_workspace_role not null,
  created_by   uuid references auth.users(id),
  created_at   timestamptz not null default now(),
  revoked_at   timestamptz,
  primary key (workspace_id, user_id)
);
create index if not exists cs_workspace_members_user_idx on public.cs_workspace_members(user_id);

-- -----------------------------------------------------------------------------
-- 5. Durable job records
-- -----------------------------------------------------------------------------
create table if not exists public.cs_runs (
  id                    uuid primary key default gen_random_uuid(),
  workspace_id          uuid not null references public.cs_workspaces(id),
  environment_id        text not null references cs_private.cs_runtime_limits(environment_id),
  plan_version_id       uuid,          -- scoped FK to cs_compile_plans is added with plans in CS-B2
  parent_run_id         uuid,
  requested_by          uuid references auth.users(id),
  state                 public.cs_run_state not null default 'queued',
  event_sequence        bigint not null default 0 check (event_sequence >= 0),
  authorization_receipt jsonb not null default '{}'::jsonb,
  cost_state            public.cs_cost_state,
  cancel_requested_at   timestamptz,
  failure_code          text,
  release_id            text not null,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  unique (workspace_id, id),
  foreign key (workspace_id, parent_run_id) references public.cs_runs(workspace_id, id)
);
create index if not exists cs_runs_workspace_created_idx on public.cs_runs(workspace_id, created_at desc);

create table if not exists public.cs_run_nodes (
  workspace_id        uuid not null,
  run_id              uuid not null,
  node_id             text not null check (node_id ~ '^[a-z0-9][a-z0-9_.-]{0,79}$'),
  state               public.cs_node_state not null default 'pending',
  input_hash          text check (input_hash ~ '^[0-9a-f]{64}$'),
  attempt_number      integer not null default 0 check (attempt_number >= 0),
  output_refs         jsonb not null default '[]'::jsonb check (jsonb_typeof(output_refs) = 'array'),
  last_event_sequence bigint not null default 0,
  reused_from_run_id  uuid,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  primary key (workspace_id, run_id, node_id),
  foreign key (workspace_id, run_id) references public.cs_runs(workspace_id, id),
  foreign key (workspace_id, reused_from_run_id) references public.cs_runs(workspace_id, id)
);

create table if not exists public.cs_run_events (
  workspace_id uuid not null,
  run_id       uuid not null,
  sequence     bigint not null check (sequence > 0),
  kind         text not null check (kind ~ '^[a-z][a-z0-9_.]{0,63}$'),
  payload      jsonb not null default '{}'::jsonb,
  occurred_at  timestamptz not null default now(),
  primary key (workspace_id, run_id, sequence),
  foreign key (workspace_id, run_id) references public.cs_runs(workspace_id, id)
);

-- Scheduling primitive. CS-B1 work is owned by run+node only; the alternative
-- operation+step owner arrives with cs_operations in CS-B2/B3 (the contract's
-- XOR check is added then; no unchecked operation UUID column is shipped now).
create table if not exists public.cs_work_items (
  id             uuid primary key default gen_random_uuid(),
  workspace_id   uuid not null,
  environment_id text not null references cs_private.cs_runtime_limits(environment_id),
  run_id         uuid not null,
  node_id        text not null,
  stage          public.cs_work_stage not null,
  stage_revision integer not null default 1 check (stage_revision > 0),
  dedupe_key     text not null check (length(dedupe_key) between 1 and 200),
  due_at         timestamptz not null default now(),
  state          public.cs_work_state not null default 'queued',
  attempt_count  integer not null default 0 check (attempt_count >= 0),
  max_attempts   integer not null default 3 check (max_attempts between 1 and 3),
  lease_owner    text,
  lease_token    uuid,
  lease_until    timestamptz,
  fencing_token  bigint not null default 0 check (fencing_token >= 0),
  heartbeat_at   timestamptz,
  payload_ref    jsonb not null default '{}'::jsonb,
  deadline_at    timestamptz,
  failure_code   text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (workspace_id, id),
  unique (environment_id, dedupe_key),
  foreign key (workspace_id, run_id, node_id) references public.cs_run_nodes(workspace_id, run_id, node_id),
  check ((state = 'leased') = (lease_token is not null and lease_until is not null and lease_owner is not null))
);
create index if not exists cs_work_items_due_idx on public.cs_work_items(environment_id, due_at)
  where state in ('queued', 'retry_wait');
create index if not exists cs_work_items_lease_idx on public.cs_work_items(environment_id, lease_until)
  where state = 'leased';

create table if not exists public.cs_stage_results (
  id             uuid primary key default gen_random_uuid(),
  workspace_id   uuid not null,
  work_item_id   uuid not null,
  fencing_token  bigint not null check (fencing_token > 0),
  result_kind    text not null check (result_kind ~ '^[a-z][a-z0-9_]{0,39}$'),
  input_sha256   text check (input_sha256 ~ '^[0-9a-f]{64}$'),
  output_refs    jsonb not null default '[]'::jsonb check (jsonb_typeof(output_refs) = 'array'),
  receipt_sha256 text not null check (receipt_sha256 ~ '^[0-9a-f]{64}$'),
  release_id     text not null,
  created_at     timestamptz not null default now(),
  unique (work_item_id, fencing_token, result_kind),
  foreign key (workspace_id, work_item_id) references public.cs_work_items(workspace_id, id)
);

create table if not exists public.cs_idempotency (
  id             uuid primary key default gen_random_uuid(),
  workspace_id   uuid references public.cs_workspaces(id),
  actor_id       uuid not null references auth.users(id),
  operation      text not null check (operation ~ '^[a-z][a-z0-9_.]{0,63}$'),
  key            uuid not null,
  request_sha256 text not null check (request_sha256 ~ '^[0-9a-f]{64}$'),
  result_ref     jsonb not null,
  expires_at     timestamptz,
  created_at     timestamptz not null default now()
);
-- Contract key for workspace-scoped commands; creation commands that precede a
-- workspace (account.create, workspace.create) are keyed by the acting user.
create unique index if not exists cs_idempotency_workspace_key
  on public.cs_idempotency(workspace_id, operation, key) where workspace_id is not null;
create unique index if not exists cs_idempotency_actor_key
  on public.cs_idempotency(actor_id, operation, key) where workspace_id is null;

-- -----------------------------------------------------------------------------
-- 6. Assets (logical) and immutable asset versions (actual bytes)
-- -----------------------------------------------------------------------------
create table if not exists public.cs_assets (
  id                uuid primary key default gen_random_uuid(),
  workspace_id      uuid not null references public.cs_workspaces(id),
  label             text not null check (length(btrim(label)) between 1 and 200),
  kind              text not null check (kind in ('source', 'generated', 'composition', 'export', 'preview', 'intermediate')),
  status            text not null default 'active' check (status in ('active', 'withdrawn')),
  latest_version_id uuid,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  unique (workspace_id, id)
);

create table if not exists public.cs_asset_versions (
  id                uuid primary key default gen_random_uuid(),
  workspace_id      uuid not null,
  asset_id          uuid not null,
  revision          integer not null check (revision > 0),
  environment_id    text not null references cs_private.cs_runtime_limits(environment_id),
  storage_key       text not null,
  sha256            text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  mime_type         text not null,
  size_bytes        bigint not null check (size_bytes > 0 and size_bytes <= 45000000),
  width             integer check (width > 0),
  height            integer check (height > 0),
  duration_ms       integer check (duration_ms > 0),
  fps_num           integer check (fps_num > 0),
  fps_den           integer check (fps_den > 0),
  source_refs       jsonb not null default '[]'::jsonb check (jsonb_typeof(source_refs) = 'array'),
  rights            jsonb not null default '{}'::jsonb,
  run_id            uuid,
  node_id           text,
  work_item_id      uuid,
  fencing_token     bigint,
  created_by        uuid references auth.users(id),
  runtime_principal text,
  created_at        timestamptz not null default now(),
  unique (workspace_id, id),
  unique (workspace_id, asset_id, revision),
  unique (environment_id, storage_key),
  foreign key (workspace_id, asset_id) references public.cs_assets(workspace_id, id),
  foreign key (workspace_id, run_id, node_id) references public.cs_run_nodes(workspace_id, run_id, node_id),
  foreign key (workspace_id, work_item_id) references public.cs_work_items(workspace_id, id),
  -- Storage identity is {environment}/{workspace}/{asset}/{version}/{sha256}.{ext}; never a URL.
  check (storage_key ~ '^[a-z0-9][a-z0-9_-]{0,39}/[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f]{64}\.[a-z0-9]{1,8}$'
     and split_part(storage_key, '/', 1) = environment_id
     and split_part(storage_key, '/', 2) = workspace_id::text
     and split_part(storage_key, '/', 3) = asset_id::text
     and split_part(storage_key, '/', 4) = id::text
     and split_part(split_part(storage_key, '/', 5), '.', 1) = sha256),
  check ((run_id is null) = (node_id is null) and (run_id is null) = (work_item_id is null))
);

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'cs_assets_latest_version_fk') then
    alter table public.cs_assets add constraint cs_assets_latest_version_fk
      foreign key (workspace_id, latest_version_id) references public.cs_asset_versions(workspace_id, id);
  end if;
end $$;

create table if not exists public.cs_audit_events (
  id                uuid primary key default gen_random_uuid(),
  workspace_id      uuid references public.cs_workspaces(id),
  account_id        uuid not null references public.cs_accounts(id),
  actor_id          uuid references auth.users(id),
  runtime_principal text,
  action            text not null check (action ~ '^[a-z][a-z0-9_.]{0,63}$'),
  input_sha256      text check (input_sha256 ~ '^[0-9a-f]{64}$'),
  affected_refs     jsonb not null default '[]'::jsonb,
  receipt_refs      jsonb not null default '[]'::jsonb,
  created_at        timestamptz not null default now(),
  check ((actor_id is null) <> (runtime_principal is null))
);
create index if not exists cs_audit_events_account_idx on public.cs_audit_events(account_id, created_at desc);

-- -----------------------------------------------------------------------------
-- 7. Storage capabilities (private schema)
-- -----------------------------------------------------------------------------
create table if not exists cs_private.cs_upload_intents (
  id                uuid primary key default gen_random_uuid(),
  workspace_id      uuid not null references public.cs_workspaces(id),
  actor_id          uuid not null references auth.users(id),
  environment_id    text not null references cs_private.cs_runtime_limits(environment_id),
  asset_id          uuid not null,
  asset_version_id  uuid not null,
  label             text not null check (length(btrim(label)) between 1 and 200),
  exact_storage_key text not null,
  expected_sha256   text not null check (expected_sha256 ~ '^[0-9a-f]{64}$'),
  max_bytes         bigint not null check (max_bytes > 0 and max_bytes <= 45000000),
  expected_mime     text not null,
  expires_at        timestamptz not null,
  finalized_at      timestamptz,
  created_at        timestamptz not null default clock_timestamp(),
  unique (environment_id, exact_storage_key),
  check (expires_at > created_at and expires_at <= created_at + interval '3600 seconds')
);

create table if not exists cs_private.cs_download_grants (
  id                uuid primary key default gen_random_uuid(),
  workspace_id      uuid not null,
  actor_id          uuid not null references auth.users(id),
  asset_version_id  uuid not null,
  exact_storage_key text not null,
  operation         text not null default 'download' check (operation = 'download'),
  expires_at        timestamptz not null,
  created_at        timestamptz not null default clock_timestamp(),
  foreign key (workspace_id, asset_version_id) references public.cs_asset_versions(workspace_id, id),
  check (expires_at > created_at and expires_at <= created_at + interval '60 seconds')
);
create index if not exists cs_download_grants_lookup_idx
  on cs_private.cs_download_grants(actor_id, exact_storage_key, expires_at);

-- -----------------------------------------------------------------------------
-- 8. Triggers: updated_at, immutable records, fixed workspace identity
-- -----------------------------------------------------------------------------
create or replace function cs_private.cs_touch() returns trigger
language plpgsql set search_path = public, pg_temp as $$
begin
  new.updated_at := now();
  return new;
end $$;

create or replace function cs_private.cs_reject_mutation() returns trigger
language plpgsql set search_path = public, pg_temp as $$
begin
  raise exception 'cs_immutable_record: % rows cannot be %', tg_table_name, lower(tg_op)
    using errcode = 'CS409';
end $$;

create or replace function cs_private.cs_workspace_identity_fixed() returns trigger
language plpgsql set search_path = public, pg_temp as $$
begin
  if new.account_id is distinct from old.account_id or new.mode is distinct from old.mode then
    raise exception 'cs_workspace_identity_fixed: account and mode are immutable' using errcode = 'CS409';
  end if;
  return new;
end $$;

do $$
declare t text;
begin
  foreach t in array array['cs_runs', 'cs_run_nodes', 'cs_work_items', 'cs_assets'] loop
    execute format('drop trigger if exists %1$s_touch on public.%1$s', t);
    execute format('create trigger %1$s_touch before update on public.%1$s for each row execute function cs_private.cs_touch()', t);
  end loop;
  foreach t in array array['cs_run_events', 'cs_stage_results', 'cs_asset_versions', 'cs_audit_events', 'cs_idempotency'] loop
    execute format('drop trigger if exists %1$s_immutable on public.%1$s', t);
    execute format('create trigger %1$s_immutable before update or delete on public.%1$s for each row execute function cs_private.cs_reject_mutation()', t);
  end loop;
end $$;

drop trigger if exists cs_workspaces_identity_fixed on public.cs_workspaces;
create trigger cs_workspaces_identity_fixed before update on public.cs_workspaces
  for each row execute function cs_private.cs_workspace_identity_fixed();

-- -----------------------------------------------------------------------------
-- 9. Identity and capability helpers
-- -----------------------------------------------------------------------------
-- The API verifies the bearer token, then sets request.jwt.claims with
-- set_config(..., true) (transaction-local). There is no p_actor anywhere.
create or replace function cs_private.cs_actor_id() returns uuid
language plpgsql stable set search_path = public, pg_temp as $$
declare
  v_raw text := nullif(current_setting('request.jwt.claims', true), '');
  v_claims jsonb;
begin
  if v_raw is null then
    return null;
  end if;
  v_claims := v_raw::jsonb;
  if coalesce(v_claims ->> 'role', '') <> 'authenticated' then
    return null;
  end if;
  return (v_claims ->> 'sub')::uuid;
exception when invalid_text_representation then
  return null;
end $$;

create or replace function cs_private.cs_require_actor() returns uuid
language plpgsql stable set search_path = public, pg_temp as $$
declare v uuid := cs_private.cs_actor_id();
begin
  if v is null then
    raise exception 'authentication_required' using errcode = 'CS401';
  end if;
  return v;
end $$;

create or replace function cs_private.cs_account_admin(p_account_id uuid) returns boolean
language sql stable security definer set search_path = public, pg_temp as $$
  select exists (
    select 1
      from public.cs_account_members m
      join public.cs_accounts a on a.id = m.account_id
     where m.account_id = p_account_id
       and m.user_id = cs_private.cs_actor_id()
       and m.revoked_at is null
       and a.status = 'active');
$$;

create or replace function cs_private.cs_account_bootstrap_allowed(p_account_id uuid) returns boolean
language sql stable security definer set search_path = public, pg_temp as $$
  select exists (select 1 from public.cs_accounts a
                  where a.id = p_account_id and a.owner_id = cs_private.cs_actor_id())
     and not exists (select 1 from public.cs_account_members m where m.account_id = p_account_id);
$$;

-- Actions: read | download | review | produce | manage. Current rows only:
-- a revoked membership or suspended workspace/account stops access immediately.
create or replace function cs_private.cs_role_allows(p_user_id uuid, p_workspace_id uuid, p_action text)
returns boolean
language sql stable security definer set search_path = public, pg_temp as $$
  select p_user_id is not null and p_action in ('read', 'download', 'review', 'produce', 'manage') and (
    exists (
      select 1
        from public.cs_workspaces w
        join public.cs_accounts a on a.id = w.account_id and a.status = 'active'
        join public.cs_account_members am on am.account_id = w.account_id
       where w.id = p_workspace_id and w.status = 'active'
         and am.user_id = p_user_id and am.revoked_at is null)
    or exists (
      select 1
        from public.cs_workspaces w
        join public.cs_accounts a on a.id = w.account_id and a.status = 'active'
        join public.cs_workspace_members m on m.workspace_id = w.id
       where w.id = p_workspace_id and w.status = 'active' and w.mode = 'native'
         and m.user_id = p_user_id and m.revoked_at is null
         and case p_action
               when 'read'     then true
               when 'download' then m.role in ('producer', 'reviewer')
               when 'review'   then m.role = 'reviewer'
               when 'produce'  then m.role = 'producer'
               else false
             end));
$$;

create or replace function cs_private.cs_can(p_workspace_id uuid, p_action text) returns boolean
language sql stable set search_path = public, pg_temp as $$
  select cs_private.cs_role_allows(cs_private.cs_actor_id(), p_workspace_id, p_action);
$$;

-- Called by storage.objects RLS as the signed-in browser user (auth.uid()).
-- select: an unexpired exact-key download grant held by this user, who still
--         has download authority; or an active runtime principal for the key's
--         environment. insert: an unexpired, unfinalized exact-key upload intent
--         held by this user, who still produces in that workspace; or the
--         runtime principal. No prefix matching, no update/delete (no upsert).
create or replace function cs_private.cs_storage_allowed(p_bucket text, p_name text, p_operation text)
returns boolean
language plpgsql stable security definer set search_path = public, pg_temp as $$
declare
  v_uid uuid := auth.uid();
begin
  if v_uid is null or p_bucket is distinct from 'creative-studio' or p_name is null then
    return false;
  end if;
  if exists (select 1 from cs_private.cs_runtime_principals p
              where p.user_id = v_uid and p.status = 'active'
                and p.environment_id = split_part(p_name, '/', 1)) then
    return p_operation in ('select', 'insert');
  end if;
  if p_operation = 'select' then
    return exists (
      select 1 from cs_private.cs_download_grants g
       where g.actor_id = v_uid and g.exact_storage_key = p_name
         and g.expires_at > clock_timestamp()
         and cs_private.cs_role_allows(v_uid, g.workspace_id, 'download'));
  elsif p_operation = 'insert' then
    return exists (
      select 1 from cs_private.cs_upload_intents u
       where u.actor_id = v_uid and u.exact_storage_key = p_name
         and u.finalized_at is null and u.expires_at > clock_timestamp()
         and cs_private.cs_role_allows(v_uid, u.workspace_id, 'produce'));
  end if;
  return false;
end $$;

-- -----------------------------------------------------------------------------
-- 10. Row level security (enabled AND forced everywhere)
-- -----------------------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array['public.cs_accounts', 'public.cs_account_members', 'public.cs_workspaces',
    'public.cs_workspace_members', 'public.cs_runs', 'public.cs_run_nodes', 'public.cs_run_events',
    'public.cs_work_items', 'public.cs_stage_results', 'public.cs_idempotency', 'public.cs_assets',
    'public.cs_asset_versions', 'public.cs_audit_events', 'cs_private.cs_runtime_limits',
    'cs_private.cs_runtime_principals', 'cs_private.cs_upload_intents', 'cs_private.cs_download_grants'] loop
    execute format('alter table %s enable row level security', t);
    execute format('alter table %s force row level security', t);
    execute format('revoke all on table %s from public, anon, authenticated', t);
  end loop;
end $$;

-- drop exactly the policies this file creates below so it re-runs cleanly; policies
-- added by later migrations (including on B1 tables, e.g. CS-B2's
-- cs_b2_api_insert on cs_work_items) are never touched (CS-INT F1)
do $$
declare r record;
begin
  for r in select p.schemaname, p.tablename, p.policyname
             from pg_policies p
             join (values
    ('public', 'cs_accounts', 'cs_api_select'),
    ('public', 'cs_accounts', 'cs_api_insert'),
    ('public', 'cs_account_members', 'cs_api_select'),
    ('public', 'cs_account_members', 'cs_api_insert'),
    ('public', 'cs_workspaces', 'cs_api_select'),
    ('public', 'cs_workspaces', 'cs_api_insert'),
    ('public', 'cs_workspace_members', 'cs_api_select'),
    ('public', 'cs_workspace_members', 'cs_api_insert'),
    ('public', 'cs_workspace_members', 'cs_api_update'),
    ('public', 'cs_runs', 'cs_api_select'),
    ('public', 'cs_run_nodes', 'cs_api_select'),
    ('public', 'cs_run_events', 'cs_api_select'),
    ('public', 'cs_assets', 'cs_api_select'),
    ('public', 'cs_asset_versions', 'cs_api_select'),
    ('public', 'cs_idempotency', 'cs_api_select'),
    ('public', 'cs_idempotency', 'cs_api_insert'),
    ('public', 'cs_audit_events', 'cs_api_select'),
    ('public', 'cs_audit_events', 'cs_api_insert'),
    ('cs_private', 'cs_runtime_limits', 'cs_api_select'),
    ('cs_private', 'cs_upload_intents', 'cs_api_select'),
    ('cs_private', 'cs_upload_intents', 'cs_api_insert'),
    ('cs_private', 'cs_download_grants', 'cs_api_select'),
    ('cs_private', 'cs_download_grants', 'cs_api_insert'),
    ('public', 'cs_runs', 'cs_worker_all'),
    ('public', 'cs_run_nodes', 'cs_worker_all'),
    ('public', 'cs_run_events', 'cs_worker_all'),
    ('public', 'cs_work_items', 'cs_worker_all'),
    ('public', 'cs_stage_results', 'cs_worker_all'),
    ('public', 'cs_assets', 'cs_worker_all'),
    ('public', 'cs_asset_versions', 'cs_worker_all'),
    ('public', 'cs_audit_events', 'cs_worker_insert'),
    ('cs_private', 'cs_runtime_limits', 'cs_worker_select'),
    ('cs_private', 'cs_upload_intents', 'cs_worker_select'),
    ('cs_private', 'cs_upload_intents', 'cs_worker_update'),
    ('public', 'cs_workspaces', 'cs_worker_select')) as own(schemaname, tablename, policyname)
               on own.schemaname = p.schemaname and own.tablename = p.tablename and own.policyname = p.policyname loop
    execute format('drop policy %I on %I.%I', r.policyname, r.schemaname, r.tablename);
  end loop;
end $$;

-- cs_api: actor/workspace/action policies
create policy cs_api_select on public.cs_accounts for select to cs_api
  using (owner_id = cs_private.cs_actor_id() or cs_private.cs_account_admin(id));
create policy cs_api_insert on public.cs_accounts for insert to cs_api
  with check (owner_id = cs_private.cs_actor_id() and status = 'active');

create policy cs_api_select on public.cs_account_members for select to cs_api
  using (user_id = cs_private.cs_actor_id() or cs_private.cs_account_admin(account_id));
create policy cs_api_insert on public.cs_account_members for insert to cs_api
  with check (cs_private.cs_account_admin(account_id)
              or (cs_private.cs_account_bootstrap_allowed(account_id)
                  and user_id = cs_private.cs_actor_id() and role = 'owner'));

create policy cs_api_select on public.cs_workspaces for select to cs_api
  using (cs_private.cs_can(id, 'read'));
create policy cs_api_insert on public.cs_workspaces for insert to cs_api
  with check (cs_private.cs_account_admin(account_id) and mode = 'native' and status = 'active'
              and created_by = cs_private.cs_actor_id());

create policy cs_api_select on public.cs_workspace_members for select to cs_api
  using (cs_private.cs_can(workspace_id, 'read'));
create policy cs_api_insert on public.cs_workspace_members for insert to cs_api
  with check (cs_private.cs_can(workspace_id, 'manage') and created_by = cs_private.cs_actor_id());
create policy cs_api_update on public.cs_workspace_members for update to cs_api
  using (cs_private.cs_can(workspace_id, 'manage'))
  with check (cs_private.cs_can(workspace_id, 'manage'));

create policy cs_api_select on public.cs_runs for select to cs_api using (cs_private.cs_can(workspace_id, 'read'));
create policy cs_api_select on public.cs_run_nodes for select to cs_api using (cs_private.cs_can(workspace_id, 'read'));
create policy cs_api_select on public.cs_run_events for select to cs_api using (cs_private.cs_can(workspace_id, 'read'));
create policy cs_api_select on public.cs_assets for select to cs_api using (cs_private.cs_can(workspace_id, 'read'));
create policy cs_api_select on public.cs_asset_versions for select to cs_api using (cs_private.cs_can(workspace_id, 'read'));

create policy cs_api_select on public.cs_idempotency for select to cs_api
  using (actor_id = cs_private.cs_actor_id());
create policy cs_api_insert on public.cs_idempotency for insert to cs_api
  with check (actor_id = cs_private.cs_actor_id()
              and (workspace_id is null or cs_private.cs_can(workspace_id, 'read')));

create policy cs_api_select on public.cs_audit_events for select to cs_api
  using (cs_private.cs_account_admin(account_id));
create policy cs_api_insert on public.cs_audit_events for insert to cs_api
  with check (actor_id = cs_private.cs_actor_id() and runtime_principal is null);

create policy cs_api_select on cs_private.cs_runtime_limits for select to cs_api using (true);

create policy cs_api_select on cs_private.cs_upload_intents for select to cs_api
  using (actor_id = cs_private.cs_actor_id() and cs_private.cs_can(workspace_id, 'read'));
create policy cs_api_insert on cs_private.cs_upload_intents for insert to cs_api
  with check (actor_id = cs_private.cs_actor_id() and cs_private.cs_can(workspace_id, 'produce'));

create policy cs_api_select on cs_private.cs_download_grants for select to cs_api
  using (actor_id = cs_private.cs_actor_id());
create policy cs_api_insert on cs_private.cs_download_grants for insert to cs_api
  with check (actor_id = cs_private.cs_actor_id() and cs_private.cs_can(workspace_id, 'download'));

-- cs_worker: intentional cross-workspace access to job/asset state only.
-- No membership, account, idempotency, grant or intent rows.
create policy cs_worker_all on public.cs_runs for all to cs_worker using (true) with check (true);
create policy cs_worker_all on public.cs_run_nodes for all to cs_worker using (true) with check (true);
create policy cs_worker_all on public.cs_run_events for all to cs_worker using (true) with check (true);
create policy cs_worker_all on public.cs_work_items for all to cs_worker using (true) with check (true);
create policy cs_worker_all on public.cs_stage_results for all to cs_worker using (true) with check (true);
create policy cs_worker_all on public.cs_assets for all to cs_worker using (true) with check (true);
create policy cs_worker_all on public.cs_asset_versions for all to cs_worker using (true) with check (true);
create policy cs_worker_insert on public.cs_audit_events for insert to cs_worker
  with check (actor_id is null and runtime_principal is not null);
create policy cs_worker_select on cs_private.cs_runtime_limits for select to cs_worker using (true);
-- finalize stage: read intents, stamp finalized_at; resolve workspace -> account for audit
create policy cs_worker_select on cs_private.cs_upload_intents for select to cs_worker using (true);
create policy cs_worker_update on cs_private.cs_upload_intents for update to cs_worker
  using (true) with check (finalized_at is not null);   -- column grant: finalized_at only
create policy cs_worker_select on public.cs_workspaces for select to cs_worker using (true);

-- -----------------------------------------------------------------------------
-- 11. Table grants (explicit; grants and RLS are both required)
-- -----------------------------------------------------------------------------
grant select, insert on public.cs_accounts, public.cs_workspaces, public.cs_idempotency to cs_api;
grant select, insert on public.cs_account_members, public.cs_workspace_members to cs_api;
grant update (role, revoked_at) on public.cs_workspace_members to cs_api;
grant select on public.cs_runs, public.cs_run_nodes, public.cs_run_events,
                public.cs_assets, public.cs_asset_versions to cs_api;
grant select, insert on public.cs_audit_events to cs_api;
grant select on cs_private.cs_runtime_limits to cs_api;
grant select, insert on cs_private.cs_upload_intents, cs_private.cs_download_grants to cs_api;

grant select on public.cs_runs to cs_worker;
grant update (state, event_sequence, failure_code, updated_at) on public.cs_runs to cs_worker;
grant select on public.cs_run_nodes to cs_worker;
grant update (state, attempt_number, output_refs, last_event_sequence, updated_at) on public.cs_run_nodes to cs_worker;
grant select, insert on public.cs_run_events, public.cs_stage_results, public.cs_asset_versions to cs_worker;
grant select on public.cs_work_items to cs_worker;
grant update (state, attempt_count, lease_owner, lease_token, lease_until, fencing_token, heartbeat_at,
              due_at, failure_code, updated_at) on public.cs_work_items to cs_worker;
grant select, insert on public.cs_assets to cs_worker;
grant update (latest_version_id, updated_at) on public.cs_assets to cs_worker;
grant insert on public.cs_audit_events to cs_worker;
grant select on cs_private.cs_runtime_limits to cs_worker;
grant select on cs_private.cs_upload_intents to cs_worker;
grant update (finalized_at) on cs_private.cs_upload_intents to cs_worker;
grant select (id, account_id, status) on public.cs_workspaces to cs_worker;

-- -----------------------------------------------------------------------------
-- 12. API procedures (SECURITY INVOKER: they run as cs_api under RLS)
-- -----------------------------------------------------------------------------
create or replace function cs_private.cs_idempotency_replay(
  p_workspace_id uuid, p_operation text, p_key uuid, p_request_sha256 text)
returns jsonb
language plpgsql set search_path = public, pg_temp as $$
declare v record;
begin
  if p_key is null then
    raise exception 'idempotency_key_required' using errcode = 'CS422';
  end if;
  if p_request_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid_request_digest' using errcode = 'CS422';
  end if;
  select request_sha256, result_ref into v
    from public.cs_idempotency i
   where i.operation = p_operation and i.key = p_key
     and ((p_workspace_id is null and i.workspace_id is null and i.actor_id = cs_private.cs_actor_id())
          or (p_workspace_id is not null and i.workspace_id = p_workspace_id));
  if not found then
    return null;
  end if;
  if v.request_sha256 <> p_request_sha256 then
    raise exception 'idempotency_conflict' using errcode = 'CS409';
  end if;
  return v.result_ref;
end $$;

create or replace function public.cs_create_account(p_name text, p_idempotency_key uuid, p_request_sha256 text)
returns table (account_id uuid, replayed boolean)
language plpgsql set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_require_actor();
  v_prior jsonb;
  v_id uuid;
begin
  v_prior := cs_private.cs_idempotency_replay(null, 'account.create', p_idempotency_key, p_request_sha256);
  if v_prior is not null then
    return query select (v_prior ->> 'account_id')::uuid, true;
    return;
  end if;
  insert into public.cs_accounts (owner_id, name) values (v_actor, btrim(p_name)) returning id into v_id;
  insert into public.cs_account_members (account_id, user_id, role) values (v_id, v_actor, 'owner');
  insert into public.cs_idempotency (workspace_id, actor_id, operation, key, request_sha256, result_ref)
  values (null, v_actor, 'account.create', p_idempotency_key, p_request_sha256, jsonb_build_object('account_id', v_id));
  insert into public.cs_audit_events (account_id, actor_id, action, input_sha256, affected_refs)
  values (v_id, v_actor, 'account.create', p_request_sha256, jsonb_build_array(jsonb_build_object('kind', 'account', 'id', v_id)));
  return query select v_id, false;
end $$;

create or replace function public.cs_create_workspace(
  p_account_id uuid, p_name text, p_idempotency_key uuid, p_request_sha256 text)
returns table (workspace_id uuid, replayed boolean)
language plpgsql set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_require_actor();
  v_prior jsonb;
  v_id uuid;
begin
  v_prior := cs_private.cs_idempotency_replay(null, 'workspace.create', p_idempotency_key, p_request_sha256);
  if v_prior is not null then
    return query select (v_prior ->> 'workspace_id')::uuid, true;
    return;
  end if;
  if not cs_private.cs_account_admin(p_account_id) then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  -- id chosen here, no RETURNING: the SELECT policy's membership lookup runs on
  -- the statement snapshot, which cannot yet see the row being inserted.
  v_id := gen_random_uuid();
  insert into public.cs_workspaces (id, account_id, name, mode, created_by)
  values (v_id, p_account_id, btrim(p_name), 'native', v_actor);
  insert into public.cs_idempotency (workspace_id, actor_id, operation, key, request_sha256, result_ref)
  values (null, v_actor, 'workspace.create', p_idempotency_key, p_request_sha256, jsonb_build_object('workspace_id', v_id));
  insert into public.cs_audit_events (account_id, workspace_id, actor_id, action, input_sha256, affected_refs)
  values (p_account_id, v_id, v_actor, 'workspace.create', p_request_sha256,
          jsonb_build_array(jsonb_build_object('kind', 'workspace', 'id', v_id)));
  return query select v_id, false;
end $$;

create or replace function public.cs_set_workspace_member(
  p_workspace_id uuid, p_user_id uuid, p_role public.cs_workspace_role,
  p_idempotency_key uuid, p_request_sha256 text)
returns table (workspace_id uuid, user_id uuid, role public.cs_workspace_role, replayed boolean)
language plpgsql set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_require_actor();
  v_prior jsonb;
  v_account uuid;
begin
  if not cs_private.cs_can(p_workspace_id, 'read') then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  if not cs_private.cs_can(p_workspace_id, 'manage') then
    raise exception 'capability_denied' using errcode = 'CS403';
  end if;
  v_prior := cs_private.cs_idempotency_replay(p_workspace_id, 'membership.set', p_idempotency_key, p_request_sha256);
  if v_prior is not null then
    return query select p_workspace_id, p_user_id, (v_prior ->> 'role')::public.cs_workspace_role, true;
    return;
  end if;
  insert into public.cs_workspace_members as m (workspace_id, user_id, role, created_by)
  values (p_workspace_id, p_user_id, p_role, v_actor)
  on conflict on constraint cs_workspace_members_pkey
  do update set role = excluded.role, revoked_at = null;
  select w.account_id into v_account from public.cs_workspaces w where w.id = p_workspace_id;
  insert into public.cs_idempotency (workspace_id, actor_id, operation, key, request_sha256, result_ref)
  values (p_workspace_id, v_actor, 'membership.set', p_idempotency_key, p_request_sha256,
          jsonb_build_object('user_id', p_user_id, 'role', p_role));
  insert into public.cs_audit_events (account_id, workspace_id, actor_id, action, input_sha256, affected_refs)
  values (v_account, p_workspace_id, v_actor, 'membership.set', p_request_sha256,
          jsonb_build_array(jsonb_build_object('kind', 'workspace_member', 'user_id', p_user_id, 'role', p_role)));
  return query select p_workspace_id, p_user_id, p_role, false;
end $$;

create or replace function public.cs_revoke_workspace_member(p_workspace_id uuid, p_user_id uuid)
returns boolean
language plpgsql set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_require_actor();
  v_account uuid;
begin
  if not cs_private.cs_can(p_workspace_id, 'read') then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  if not cs_private.cs_can(p_workspace_id, 'manage') then
    raise exception 'capability_denied' using errcode = 'CS403';
  end if;
  update public.cs_workspace_members m set revoked_at = now()
   where m.workspace_id = p_workspace_id and m.user_id = p_user_id and m.revoked_at is null;
  if not found then
    return false;
  end if;
  select w.account_id into v_account from public.cs_workspaces w where w.id = p_workspace_id;
  insert into public.cs_audit_events (account_id, workspace_id, actor_id, action, affected_refs)
  values (v_account, p_workspace_id, v_actor, 'membership.revoke',
          jsonb_build_array(jsonb_build_object('kind', 'workspace_member', 'user_id', p_user_id)));
  return true;
end $$;

create or replace function cs_private.cs_extension_for_mime(p_mime text) returns text
language sql immutable set search_path = public, pg_temp as $$
  select case p_mime
           when 'image/png'  then 'png'
           when 'image/jpeg' then 'jpg'
           when 'image/webp' then 'webp'
           when 'video/mp4'  then 'mp4'
           when 'audio/mp4'  then 'm4a'
           when 'font/ttf'   then 'ttf'
           when 'font/otf'   then 'otf'
           else null
         end;
$$;

create or replace function public.cs_create_upload_intent(
  p_workspace_id uuid, p_environment_id text, p_label text, p_mime_type text, p_size_bytes bigint,
  p_expected_sha256 text, p_idempotency_key uuid, p_request_sha256 text)
returns table (upload_id uuid, asset_id uuid, asset_version_id uuid, storage_key text,
               expires_at timestamptz, replayed boolean)
language plpgsql set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_require_actor();
  v_prior jsonb;
  v_limits record;
  v_ext text := cs_private.cs_extension_for_mime(p_mime_type);
  v_asset uuid := gen_random_uuid();
  v_version uuid := gen_random_uuid();
  v_key text;
  v_now timestamptz := clock_timestamp();
  v_row record;
begin
  if not cs_private.cs_can(p_workspace_id, 'read') then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  if not cs_private.cs_can(p_workspace_id, 'produce') then
    raise exception 'capability_denied' using errcode = 'CS403';
  end if;
  v_prior := cs_private.cs_idempotency_replay(p_workspace_id, 'upload.create', p_idempotency_key, p_request_sha256);
  if v_prior is not null then
    select u.id, u.asset_id, u.asset_version_id, u.exact_storage_key, u.expires_at into v_row
      from cs_private.cs_upload_intents u where u.id = (v_prior ->> 'upload_id')::uuid;
    return query select v_row.id, v_row.asset_id, v_row.asset_version_id, v_row.exact_storage_key, v_row.expires_at, true;
    return;
  end if;
  select * into v_limits from cs_private.cs_runtime_limits l where l.environment_id = p_environment_id;
  if not found then
    raise exception 'environment_unavailable' using errcode = 'CS503';
  end if;
  if v_ext is null then
    raise exception 'unsupported_media_type' using errcode = 'CS422';
  end if;
  if p_size_bytes is null or p_size_bytes <= 0 or p_size_bytes > 45000000 then
    raise exception 'object_size_out_of_bounds' using errcode = 'CS422';
  end if;
  if p_expected_sha256 is null or p_expected_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid_sha256' using errcode = 'CS422';
  end if;
  v_key := format('%s/%s/%s/%s/%s.%s', p_environment_id, p_workspace_id, v_asset, v_version, p_expected_sha256, v_ext);
  insert into cs_private.cs_upload_intents as u (workspace_id, actor_id, environment_id, asset_id, asset_version_id,
    label, exact_storage_key, expected_sha256, max_bytes, expected_mime, expires_at, created_at)
  values (p_workspace_id, v_actor, p_environment_id, v_asset, v_version, btrim(p_label), v_key, p_expected_sha256,
          p_size_bytes, p_mime_type, v_now + make_interval(secs => v_limits.upload_intent_seconds), v_now)
  returning u.id, u.expires_at into v_row;
  insert into public.cs_idempotency (workspace_id, actor_id, operation, key, request_sha256, result_ref)
  values (p_workspace_id, v_actor, 'upload.create', p_idempotency_key, p_request_sha256,
          jsonb_build_object('upload_id', v_row.id));
  insert into public.cs_audit_events (account_id, workspace_id, actor_id, action, input_sha256, affected_refs)
  select w.account_id, p_workspace_id, v_actor, 'upload.create', p_request_sha256,
         jsonb_build_array(jsonb_build_object('kind', 'upload_intent', 'id', v_row.id))
    from public.cs_workspaces w where w.id = p_workspace_id;
  return query select v_row.id, v_asset, v_version, v_key, v_row.expires_at, false;
end $$;

create or replace function public.cs_create_download_grant(
  p_workspace_id uuid, p_asset_id uuid, p_asset_version_id uuid, p_expected_sha256 text, p_seconds integer)
returns table (grant_id uuid, storage_key text, sha256 text, mime_type text, size_bytes bigint, expires_at timestamptz)
language plpgsql set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_require_actor();
  v_version record;
  v_now timestamptz := clock_timestamp();
  v_grant record;
begin
  if not cs_private.cs_can(p_workspace_id, 'read') then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  if not cs_private.cs_can(p_workspace_id, 'download') then
    raise exception 'capability_denied' using errcode = 'CS403';
  end if;
  if p_seconds is null or p_seconds < 1 or p_seconds > 60 then
    raise exception 'grant_ttl_out_of_bounds' using errcode = 'CS422';
  end if;
  select v.id, v.storage_key, v.sha256, v.mime_type, v.size_bytes into v_version
    from public.cs_asset_versions v
    join public.cs_assets a on a.workspace_id = v.workspace_id and a.id = v.asset_id
   where v.workspace_id = p_workspace_id and v.asset_id = p_asset_id and v.id = p_asset_version_id
     and a.status = 'active';
  if not found then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  if p_expected_sha256 is distinct from v_version.sha256 then
    raise exception 'asset_hash_mismatch' using errcode = 'CS422';
  end if;
  insert into cs_private.cs_download_grants as g (workspace_id, actor_id, asset_version_id, exact_storage_key, expires_at, created_at)
  values (p_workspace_id, v_actor, v_version.id, v_version.storage_key, v_now + make_interval(secs => p_seconds), v_now)
  returning g.id, g.expires_at into v_grant;
  return query select v_grant.id, v_version.storage_key, v_version.sha256, v_version.mime_type, v_version.size_bytes, v_grant.expires_at;
end $$;

-- -----------------------------------------------------------------------------
-- 13. Worker scheduling primitive (SECURITY INVOKER: runs as cs_worker)
-- -----------------------------------------------------------------------------
-- Stages whose expired lease must NOT be silently re-claimed: an interrupted
-- billable dispatch goes to reconciliation (CS-B3), never blind resubmission.
create or replace function cs_private.cs_paid_stages() returns public.cs_work_stage[]
language sql immutable set search_path = public, pg_temp as $$
  select array['provider_submit', 'provider_reconcile_submission', 'provider_cancel', 'author_treatment']::public.cs_work_stage[];
$$;

create or replace function cs_private.cs_append_run_event(
  p_workspace_id uuid, p_run_id uuid, p_kind text, p_payload jsonb)
returns bigint
language plpgsql set search_path = public, pg_temp as $$
declare v_seq bigint;
begin
  update public.cs_runs r set event_sequence = r.event_sequence + 1
   where r.workspace_id = p_workspace_id and r.id = p_run_id
  returning r.event_sequence into v_seq;           -- run row lock serialises the sequence
  if v_seq is null then
    raise exception 'run_missing' using errcode = 'CS404';
  end if;
  insert into public.cs_run_events (workspace_id, run_id, sequence, kind, payload)
  values (p_workspace_id, p_run_id, v_seq, p_kind, coalesce(p_payload, '{}'::jsonb));
  return v_seq;
end $$;

create or replace function public.cs_claim_work(
  p_environment_id text, p_worker_id text, p_stages public.cs_work_stage[], p_limit integer)
returns table (work_item_id uuid, workspace_id uuid, run_id uuid, node_id text, stage public.cs_work_stage,
               stage_revision integer, payload_ref jsonb, attempt_count integer,
               lease_token uuid, fencing_token bigint, lease_until timestamptz)
language plpgsql set search_path = public, pg_temp as $$
declare
  v_limits record;
  v_item record;
begin
  if p_worker_id is null or p_worker_id !~ '^[A-Za-z0-9_.:-]{1,100}$' then
    raise exception 'invalid_worker_id' using errcode = 'CS422';
  end if;
  select * into v_limits from cs_private.cs_runtime_limits l where l.environment_id = p_environment_id;
  if not found then
    raise exception 'environment_unavailable' using errcode = 'CS503';
  end if;
  if not v_limits.dispatch_enabled then
    raise exception 'dispatch_disabled' using errcode = 'CS503';
  end if;
  if p_limit is null or p_limit < 1 or p_limit > v_limits.max_dispatch_batch then
    raise exception 'claim_limit_out_of_bounds' using errcode = 'CS422';
  end if;
  for v_item in
    with picked as (
      select w.id
        from public.cs_work_items w
       where w.environment_id = p_environment_id
         and w.stage = any(coalesce(p_stages, array[]::public.cs_work_stage[]))
         and ((w.state in ('queued', 'retry_wait') and w.due_at <= now())
              or (w.state = 'leased' and w.lease_until <= now() and w.stage <> all(cs_private.cs_paid_stages())))
         and w.attempt_count < w.max_attempts
       order by w.due_at, w.id
       limit p_limit
       for update skip locked)
    update public.cs_work_items w
       set state = 'leased',
           fencing_token = w.fencing_token + 1,
           lease_token = gen_random_uuid(),
           lease_owner = p_worker_id,
           lease_until = now() + interval '360 seconds',
           heartbeat_at = null,
           attempt_count = w.attempt_count + 1
      from picked where w.id = picked.id
    returning w.*
  loop
    update public.cs_run_nodes n set state = 'running', attempt_number = v_item.attempt_count
     where n.workspace_id = v_item.workspace_id and n.run_id = v_item.run_id and n.node_id = v_item.node_id;
    update public.cs_runs r set state = 'running'
     where r.workspace_id = v_item.workspace_id and r.id = v_item.run_id and r.state = 'queued';
    perform cs_private.cs_append_run_event(v_item.workspace_id, v_item.run_id, 'work.claimed',
      jsonb_build_object('node_id', v_item.node_id, 'stage', v_item.stage, 'fencing_token', v_item.fencing_token));
    work_item_id := v_item.id; workspace_id := v_item.workspace_id; run_id := v_item.run_id;
    node_id := v_item.node_id; stage := v_item.stage; stage_revision := v_item.stage_revision;
    payload_ref := v_item.payload_ref; attempt_count := v_item.attempt_count;
    lease_token := v_item.lease_token; fencing_token := v_item.fencing_token; lease_until := v_item.lease_until;
    return next;
  end loop;
end $$;

-- Lease CAS used by every worker mutation. Locks the item row.
create or replace function cs_private.cs_lock_live_lease(p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint)
returns public.cs_work_items
language plpgsql set search_path = public, pg_temp as $$
declare v public.cs_work_items;
begin
  select * into v from public.cs_work_items w where w.id = p_work_item_id for update;
  if not found or v.state <> 'leased' or v.lease_token is distinct from p_lease_token
     or v.fencing_token is distinct from p_fencing_token or v.lease_until <= clock_timestamp() then
    raise exception 'lease_lost' using errcode = 'CS409';
  end if;
  return v;
end $$;

create or replace function public.cs_heartbeat_work(p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint)
returns timestamptz
language plpgsql set search_path = public, pg_temp as $$
declare v public.cs_work_items;
begin
  v := cs_private.cs_lock_live_lease(p_work_item_id, p_lease_token, p_fencing_token);
  update public.cs_work_items w set heartbeat_at = clock_timestamp() where w.id = v.id;
  return v.lease_until;          -- hard lease is never extended
end $$;

-- Validates JSON-held AssetRefs: same workspace, existing version, matching digest.
create or replace function cs_private.cs_assert_asset_refs(p_workspace_id uuid, p_refs jsonb)
returns void
language plpgsql set search_path = public, pg_temp as $$
declare r jsonb;
begin
  if p_refs is null or jsonb_typeof(p_refs) <> 'array' then
    raise exception 'invalid_output_refs' using errcode = 'CS422';
  end if;
  for r in select * from jsonb_array_elements(p_refs) loop
    if not exists (
      select 1 from public.cs_asset_versions v
       where v.workspace_id = p_workspace_id
         and (r ->> 'workspace_id') = p_workspace_id::text
         and v.id::text = (r ->> 'asset_version_id')
         and v.asset_id::text = (r ->> 'asset_id')
         and v.sha256 = (r ->> 'sha256')) then
      raise exception 'output_ref_unresolved' using errcode = 'CS422';
    end if;
  end loop;
end $$;

create or replace function public.cs_commit_asset_version(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint,
  p_asset_id uuid, p_label text, p_kind text, p_mime_type text, p_size_bytes bigint, p_sha256 text,
  p_width integer, p_height integer, p_duration_ms integer, p_source_refs jsonb, p_runtime_principal text)
returns table (asset_id uuid, asset_version_id uuid, revision integer, storage_key text)
language plpgsql set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
  v_ext text := cs_private.cs_extension_for_mime(p_mime_type);
  v_asset uuid := coalesce(p_asset_id, gen_random_uuid());
  v_version uuid := gen_random_uuid();
  v_rev integer;
  v_key text;
begin
  v := cs_private.cs_lock_live_lease(p_work_item_id, p_lease_token, p_fencing_token);
  if v_ext is null then
    raise exception 'unsupported_media_type' using errcode = 'CS422';
  end if;
  if p_runtime_principal is null or length(p_runtime_principal) = 0 then
    raise exception 'runtime_principal_required' using errcode = 'CS422';
  end if;
  perform cs_private.cs_assert_asset_refs(v.workspace_id, coalesce(p_source_refs, '[]'::jsonb));
  if p_asset_id is null then
    insert into public.cs_assets (id, workspace_id, label, kind) values (v_asset, v.workspace_id, btrim(p_label), p_kind);
  elsif not exists (select 1 from public.cs_assets a where a.workspace_id = v.workspace_id and a.id = p_asset_id
                     for update) then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  select coalesce(max(av.revision), 0) + 1 into v_rev
    from public.cs_asset_versions av where av.workspace_id = v.workspace_id and av.asset_id = v_asset;
  v_key := format('%s/%s/%s/%s/%s.%s', v.environment_id, v.workspace_id, v_asset, v_version, p_sha256, v_ext);
  insert into public.cs_asset_versions (id, workspace_id, asset_id, revision, environment_id, storage_key, sha256,
    mime_type, size_bytes, width, height, duration_ms, source_refs, run_id, node_id, work_item_id, fencing_token,
    runtime_principal)
  values (v_version, v.workspace_id, v_asset, v_rev, v.environment_id, v_key, p_sha256, p_mime_type, p_size_bytes,
          p_width, p_height, p_duration_ms, coalesce(p_source_refs, '[]'::jsonb), v.run_id, v.node_id, v.id,
          v.fencing_token, p_runtime_principal);
  update public.cs_assets a set latest_version_id = v_version where a.workspace_id = v.workspace_id and a.id = v_asset;
  return query select v_asset, v_version, v_rev, v_key;
end $$;

create or replace function public.cs_finish_work(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_result_kind text,
  p_input_sha256 text, p_output_refs jsonb, p_receipt_sha256 text, p_release_id text)
returns uuid
language plpgsql set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
  v_result uuid;
  v_seq bigint;
begin
  v := cs_private.cs_lock_live_lease(p_work_item_id, p_lease_token, p_fencing_token);
  perform cs_private.cs_assert_asset_refs(v.workspace_id, p_output_refs);
  insert into public.cs_stage_results (workspace_id, work_item_id, fencing_token, result_kind, input_sha256,
    output_refs, receipt_sha256, release_id)
  values (v.workspace_id, v.id, v.fencing_token, p_result_kind, p_input_sha256, p_output_refs, p_receipt_sha256, p_release_id)
  returning id into v_result;
  update public.cs_work_items w
     set state = 'done', lease_token = null, lease_until = null, lease_owner = null, failure_code = null
   where w.id = v.id;
  v_seq := cs_private.cs_append_run_event(v.workspace_id, v.run_id, 'work.finished',
    jsonb_build_object('node_id', v.node_id, 'stage', v.stage, 'stage_result_id', v_result,
                       'fencing_token', v.fencing_token));
  -- Node output is recorded; downstream release and run completion belong to
  -- the CS-B3 DAG scheduler. The run is NOT marked completed here.
  update public.cs_run_nodes n set state = 'succeeded', output_refs = p_output_refs, last_event_sequence = v_seq
   where n.workspace_id = v.workspace_id and n.run_id = v.run_id and n.node_id = v.node_id;
  return v_result;
end $$;

create or replace function public.cs_reschedule_work(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_failure_code text, p_delay_seconds integer)
returns public.cs_work_state
language plpgsql set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
  v_next public.cs_work_state;
  v_seq bigint;
begin
  v := cs_private.cs_lock_live_lease(p_work_item_id, p_lease_token, p_fencing_token);
  if p_failure_code is null or p_failure_code !~ '^[a-z][a-z0-9_]{0,63}$' then
    raise exception 'invalid_failure_code' using errcode = 'CS422';
  end if;
  if p_delay_seconds is null or p_delay_seconds < 1 or p_delay_seconds > 3600 then
    raise exception 'delay_out_of_bounds' using errcode = 'CS422';
  end if;
  v_next := case when v.attempt_count >= v.max_attempts then 'dead' else 'retry_wait' end;
  update public.cs_work_items w
     set state = v_next, lease_token = null, lease_until = null, lease_owner = null,
         failure_code = p_failure_code, due_at = now() + make_interval(secs => p_delay_seconds)
   where w.id = v.id;
  v_seq := cs_private.cs_append_run_event(v.workspace_id, v.run_id,
    case when v_next = 'dead' then 'work.dead' else 'work.retry_scheduled' end,
    jsonb_build_object('node_id', v.node_id, 'stage', v.stage, 'failure_code', p_failure_code,
                       'attempt_count', v.attempt_count));
  update public.cs_run_nodes n
     set state = case when v_next = 'dead' then 'failed'::public.cs_node_state else 'queued'::public.cs_node_state end,
         last_event_sequence = v_seq
   where n.workspace_id = v.workspace_id and n.run_id = v.run_id and n.node_id = v.node_id;
  return v_next;
end $$;

-- Upload finalize (worker). The worker has already read the stored object with
-- its runtime principal and computed the observed digest/size/type; the DB
-- refuses anything that does not match the intent exactly. Idempotent: a
-- repeated finalize of the same bytes returns the saved version.
create or replace function public.cs_finalize_asset(
  p_upload_id uuid, p_observed_sha256 text, p_observed_size bigint, p_observed_mime text,
  p_width integer, p_height integer, p_duration_ms integer, p_runtime_principal text)
returns table (asset_id uuid, asset_version_id uuid, revision integer, storage_key text, replayed boolean)
language plpgsql set search_path = public, pg_temp as $$
declare
  u cs_private.cs_upload_intents;
  v_account uuid;
begin
  if p_runtime_principal is null or p_runtime_principal !~ '^[A-Za-z0-9_.:-]{1,100}$' then
    raise exception 'runtime_principal_required' using errcode = 'CS422';
  end if;
  select * into u from cs_private.cs_upload_intents i where i.id = p_upload_id for update;
  if not found then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  if p_observed_sha256 is distinct from u.expected_sha256 then
    raise exception 'asset_hash_mismatch' using errcode = 'CS422';
  end if;
  if p_observed_mime is distinct from u.expected_mime then
    raise exception 'media_type_mismatch' using errcode = 'CS422';
  end if;
  if p_observed_size is null or p_observed_size <= 0 or p_observed_size > u.max_bytes then
    raise exception 'object_size_out_of_bounds' using errcode = 'CS422';
  end if;
  if u.finalized_at is not null then
    return query select v.asset_id, v.id, v.revision, v.storage_key, true
      from public.cs_asset_versions v where v.workspace_id = u.workspace_id and v.id = u.asset_version_id;
    return;
  end if;
  select w.account_id into v_account from public.cs_workspaces w where w.id = u.workspace_id;
  insert into public.cs_assets (id, workspace_id, label, kind)
  values (u.asset_id, u.workspace_id, u.label, 'source');
  insert into public.cs_asset_versions (id, workspace_id, asset_id, revision, environment_id, storage_key, sha256,
    mime_type, size_bytes, width, height, duration_ms, created_by, runtime_principal)
  values (u.asset_version_id, u.workspace_id, u.asset_id, 1, u.environment_id, u.exact_storage_key, u.expected_sha256,
          u.expected_mime, p_observed_size, p_width, p_height, p_duration_ms, u.actor_id, p_runtime_principal);
  update public.cs_assets a set latest_version_id = u.asset_version_id
   where a.workspace_id = u.workspace_id and a.id = u.asset_id;
  update cs_private.cs_upload_intents i set finalized_at = clock_timestamp() where i.id = u.id;
  insert into public.cs_audit_events (account_id, workspace_id, runtime_principal, action, input_sha256, affected_refs)
  values (v_account, u.workspace_id, p_runtime_principal, 'asset.finalize', u.expected_sha256,
          jsonb_build_array(jsonb_build_object('kind', 'asset_version', 'id', u.asset_version_id),
                            jsonb_build_object('kind', 'upload_intent', 'id', u.id)));
  return query select u.asset_id, u.asset_version_id, 1, u.exact_storage_key, false;
end $$;

-- -----------------------------------------------------------------------------
-- 14. Function privileges: nothing executable by PUBLIC/anon/authenticated
--     except the one storage helper authenticated Storage RLS needs.
-- -----------------------------------------------------------------------------
do $$
declare r record;
begin
  -- B1's own functions only (CS-INT F1); later migrations manage their own privileges
  for r in select p.oid::regprocedure as sig
             from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname || '.' || p.proname = any (array[
      'cs_private.cs_account_admin', 'cs_private.cs_account_bootstrap_allowed', 'cs_private.cs_actor_id',
      'cs_private.cs_append_run_event', 'cs_private.cs_assert_asset_refs', 'cs_private.cs_can',
      'cs_private.cs_extension_for_mime', 'cs_private.cs_idempotency_replay', 'cs_private.cs_lock_live_lease',
      'cs_private.cs_paid_stages', 'cs_private.cs_reject_mutation', 'cs_private.cs_require_actor',
      'cs_private.cs_role_allows', 'cs_private.cs_storage_allowed', 'cs_private.cs_touch',
      'cs_private.cs_workspace_identity_fixed', 'public.cs_claim_work', 'public.cs_commit_asset_version',
      'public.cs_create_account', 'public.cs_create_download_grant', 'public.cs_create_upload_intent',
      'public.cs_create_workspace', 'public.cs_finalize_asset', 'public.cs_finish_work',
      'public.cs_heartbeat_work', 'public.cs_reschedule_work', 'public.cs_revoke_workspace_member',
      'public.cs_set_workspace_member']) loop
    execute format('revoke all on function %s from public, anon, authenticated', r.sig);
  end loop;
end $$;

grant execute on function cs_private.cs_storage_allowed(text, text, text) to authenticated;
grant execute on function cs_private.cs_actor_id(), cs_private.cs_require_actor(),
  cs_private.cs_account_admin(uuid), cs_private.cs_account_bootstrap_allowed(uuid),
  cs_private.cs_role_allows(uuid, uuid, text), cs_private.cs_can(uuid, text),
  cs_private.cs_idempotency_replay(uuid, text, uuid, text), cs_private.cs_extension_for_mime(text)
  to cs_api;
grant execute on function public.cs_create_account(text, uuid, text),
  public.cs_create_workspace(uuid, text, uuid, text),
  public.cs_set_workspace_member(uuid, uuid, public.cs_workspace_role, uuid, text),
  public.cs_revoke_workspace_member(uuid, uuid),
  public.cs_create_upload_intent(uuid, text, text, text, bigint, text, uuid, text),
  public.cs_create_download_grant(uuid, uuid, uuid, text, integer)
  to cs_api;
grant execute on function cs_private.cs_paid_stages(), cs_private.cs_append_run_event(uuid, uuid, text, jsonb),
  cs_private.cs_lock_live_lease(uuid, uuid, bigint), cs_private.cs_assert_asset_refs(uuid, jsonb),
  cs_private.cs_extension_for_mime(text)
  to cs_worker;
grant execute on function public.cs_claim_work(text, text, public.cs_work_stage[], integer),
  public.cs_heartbeat_work(uuid, uuid, bigint),
  public.cs_commit_asset_version(uuid, uuid, bigint, uuid, text, text, text, bigint, text, integer, integer, integer, jsonb, text),
  public.cs_finish_work(uuid, uuid, bigint, text, text, jsonb, text, text),
  public.cs_reschedule_work(uuid, uuid, bigint, text, integer),
  public.cs_finalize_asset(uuid, text, bigint, text, integer, integer, integer, text)
  to cs_worker;
grant execute on function cs_private.cs_touch(), cs_private.cs_reject_mutation(),
  cs_private.cs_workspace_identity_fixed() to cs_api, cs_worker;

-- -----------------------------------------------------------------------------
-- 15. Private bucket + exact-key Storage policies (no update/delete => no upsert)
-- -----------------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('creative-studio', 'creative-studio', false, 50000000,
        array['image/png', 'image/jpeg', 'image/webp', 'video/mp4', 'audio/mp4', 'font/ttf', 'font/otf'])
on conflict (id) do nothing;

do $$
begin
  if exists (select 1 from storage.buckets where id = 'creative-studio' and public) then
    raise exception 'cs_b1: creative-studio bucket exists and is public; refusing';
  end if;
end $$;

drop policy if exists cs_creative_studio_select on storage.objects;
create policy cs_creative_studio_select on storage.objects for select to authenticated
  using (bucket_id = 'creative-studio' and cs_private.cs_storage_allowed(bucket_id, name, 'select'));
drop policy if exists cs_creative_studio_insert on storage.objects;
create policy cs_creative_studio_insert on storage.objects for insert to authenticated
  with check (bucket_id = 'creative-studio' and cs_private.cs_storage_allowed(bucket_id, name, 'insert'));

-- -----------------------------------------------------------------------------
-- 16. Re-run guard, restore (CS-INT F1): any cs_* function body that existed
--     before this file ran and was replaced by it is put back verbatim, so a
--     re-run never reverts a later migration's version. CREATE OR REPLACE keeps
--     owner, ACL and comment.
-- -----------------------------------------------------------------------------
do $$
declare r record;
begin
  for r in select s.fn_def from pg_temp.cs_b1_function_snapshot s
            where exists (select 1 from pg_proc p where p.oid = s.fn_oid)
              and pg_get_functiondef(s.fn_oid) is distinct from s.fn_def loop
    execute r.fn_def;
  end loop;
end $$;
drop table if exists pg_temp.cs_b1_function_snapshot;
