-- =============================================================================
-- CS-B3 / NS-10805 / PT-9641 — provider execution, admission, receipts,
-- retries/cancellation, lease recovery and cost accounting.
--
-- Builds on CS-B1 (20261030210000_cs_b1_platform_foundation.sql); never edits
-- it. Implements the CS-B3 subset of docs/creative-studio/platform/
-- DATABASE_CONTRACT.md + database-contract.json and contracts/contract.md §8–§9:
--   * global provider catalogue versions (prices derived from the adapter's own
--     `generate cost` quote x a cited credit price; never literals here);
--   * versioned credentials (workspace / platform), ciphertext only;
--   * owner-approved budget policies, per-node reservations, immutable ledger;
--   * admission: quote -> account budget lock -> reserve -> admit, or a typed
--     refusal (no run row on refusal);
--   * immutable attempts (+ append-only attempt events), frozen dispatch
--     payload written BEFORE the provider call, provider receipts with charged
--     credits and certainty, settlement against the reservation;
--   * draft -> final tier: final admission requires an approved draft output
--     used as a reference input;
--   * cancellation, lease recovery after worker death (an interrupted paid
--     dispatch becomes needs_reconciliation, never a blind resubmission),
--     operator reconciliation, run DAG advance.
--
-- Money: integer USD micros. Unknown charge is never zero: the reservation
-- ceiling stays held as unresolved exposure until evidence or an operator
-- reconciliation (actor + rationale) resolves it.
--
-- Security: every mutating procedure is SECURITY DEFINER with
-- search_path = public, pg_temp, no PUBLIC/anon/authenticated EXECUTE, actor
-- from the verified request claims (cs_private.cs_actor_id), worker calls bound
-- to a live lease (cs_private.cs_lock_live_lease). No p_actor on API paths.
-- Re-runnable: IF NOT EXISTS / OR REPLACE / drop+create policies and triggers.
-- =============================================================================

do $$
begin
  if to_regclass('public.cs_work_items') is null or to_regprocedure('cs_private.cs_lock_live_lease(uuid,uuid,bigint)') is null then
    raise exception 'cs_b3: CS-B1 foundation migration is required first';
  end if;
  if not exists (select 1 from pg_roles where rolname = current_user and (rolsuper or rolbypassrls)) then
    raise exception 'cs_b3: migration owner % must be superuser or BYPASSRLS', current_user;
  end if;
end $$;

-- -----------------------------------------------------------------------------
-- 1. Enumerations (values verbatim from contract.md §9 / database-contract.json)
-- -----------------------------------------------------------------------------
do $$
begin
  if to_regtype('public.cs_attempt_state') is null then
    create type public.cs_attempt_state as enum ('prepared', 'dispatching', 'submitted', 'waiting_provider',
      'ingesting', 'succeeded', 'failed', 'uncertain', 'cancel_requested', 'cancelled');
  end if;
  if to_regtype('public.cs_reservation_state') is null then
    create type public.cs_reservation_state as enum ('reserved', 'settled', 'unknown', 'released');
  end if;
  if to_regtype('public.cs_credential_mode') is null then
    create type public.cs_credential_mode as enum ('workspace', 'platform');
  end if;
  if to_regtype('public.cs_render_tier') is null then
    create type public.cs_render_tier as enum ('draft', 'final');
  end if;
end $$;

-- -----------------------------------------------------------------------------
-- 2. Provider catalogue (global, server-owned allowlist; immutable versions)
-- -----------------------------------------------------------------------------
create table if not exists public.cs_provider_catalogue_versions (
  id                          uuid primary key default gen_random_uuid(),
  provider                    text not null check (provider ~ '^[a-z][a-z0-9_-]{0,79}$'),
  entry_id                    text not null check (entry_id ~ '^[a-z][a-z0-9_-]{0,63}$'),
  model_id                    text not null check (length(model_id) between 1 and 160),
  endpoint_key                text not null check (endpoint_key ~ '^[a-z][a-z0-9_-]{0,63}$'),
  surface                     text not null check (surface in ('generate', 'marketing_studio')),
  operation                   text not null check (operation in ('image_generate', 'image_edit', 'video_generate', 'image_upscale')),
  tier                        public.cs_render_tier not null,
  enabled                     boolean not null,
  credential_modes            public.cs_credential_mode[] not null check (cardinality(credential_modes) between 1 and 2),
  entry                       jsonb not null check (jsonb_typeof(entry) = 'object'),
  adapter_config              jsonb not null default '{}'::jsonb check (jsonb_typeof(adapter_config) = 'object'),
  price_snapshot              jsonb not null check (price_snapshot ? 'source_url' and price_snapshot ? 'quote_source'
                                                   and price_snapshot ? 'credit_price_micros' and price_snapshot ? 'observed_at'),
  credit_price_micros         bigint not null check (credit_price_micros > 0),
  unit_cost_micros            bigint not null check (unit_cost_micros > 0),
  max_units_per_request       integer not null check (max_units_per_request > 0),
  maximum_request_cost_micros bigint not null check (maximum_request_cost_micros >= unit_cost_micros),
  price_expires_at            timestamptz not null,
  valid_until                 timestamptz not null,
  sha256                      text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  published_at                timestamptz not null default clock_timestamp(),
  unique (provider, entry_id, sha256)
);
create index if not exists cs_provider_catalogue_lookup_idx
  on public.cs_provider_catalogue_versions(provider, entry_id, published_at desc);

-- -----------------------------------------------------------------------------
-- 3. Credentials (ciphertext only; key material is never in the database)
-- -----------------------------------------------------------------------------
create table if not exists cs_private.cs_credentials (
  version_id                   uuid primary key default gen_random_uuid(),
  id                           uuid not null,
  workspace_id                 uuid references public.cs_workspaces(id),
  environment_id               text not null references cs_private.cs_runtime_limits(environment_id),
  provider                     text not null check (provider ~ '^[a-z][a-z0-9_-]{0,79}$'),
  mode                         public.cs_credential_mode not null,
  provider_account_fingerprint text not null check (provider_account_fingerprint ~ '^[A-Za-z0-9_.:-]{1,128}$'),
  ciphertext                   bytea not null check (length(ciphertext) between 16 and 65536),
  nonce                        bytea not null check (length(nonce) between 12 and 32),
  encryption_key_version       text not null check (encryption_key_version ~ '^[A-Za-z0-9_.-]{1,40}$'),
  status                       text not null default 'active' check (status in ('active', 'revoked')),
  created_by                   uuid references auth.users(id),
  created_at                   timestamptz not null default clock_timestamp(),
  revoked_at                   timestamptz,
  check ((mode = 'workspace') = (workspace_id is not null)),
  check ((status = 'revoked') = (revoked_at is not null))
);
create unique index if not exists cs_credentials_one_active_workspace
  on cs_private.cs_credentials(workspace_id, provider) where status = 'active' and mode = 'workspace';
create unique index if not exists cs_credentials_one_active_platform
  on cs_private.cs_credentials(environment_id, provider) where status = 'active' and mode = 'platform';

-- -----------------------------------------------------------------------------
-- 4. Budget policies (owner-approved, immutable revisions; zero = not approved)
-- -----------------------------------------------------------------------------
create table if not exists public.cs_budget_policies (
  id                     uuid primary key default gen_random_uuid(),
  account_id             uuid not null references public.cs_accounts(id),
  workspace_id           uuid,
  revision               integer not null check (revision > 0),
  funding_source         public.cs_credential_mode not null,
  window_seconds         integer not null check (window_seconds > 0),
  ceiling_micros         bigint not null default 0 check (ceiling_micros >= 0),
  per_run_ceiling_micros bigint not null default 0 check (per_run_ceiling_micros >= 0),
  authorization_ref      jsonb not null,
  approved_by            uuid not null references auth.users(id),
  effective_at           timestamptz not null default clock_timestamp(),
  expires_at             timestamptz,
  foreign key (account_id, workspace_id) references public.cs_workspaces(account_id, id),
  unique nulls not distinct (account_id, workspace_id, revision)
);

-- -----------------------------------------------------------------------------
-- 5. Run admission records (immutable)
-- -----------------------------------------------------------------------------
create table if not exists public.cs_tier_approvals (
  id               uuid primary key default gen_random_uuid(),
  workspace_id     uuid not null,
  run_id           uuid not null,
  node_id          text not null,
  asset_id         uuid not null,
  asset_version_id uuid not null,
  sha256           text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  approved_by      uuid not null references auth.users(id),
  rationale        text check (rationale is null or length(rationale) between 1 and 2000),
  created_at       timestamptz not null default clock_timestamp(),
  unique (workspace_id, id),
  unique (workspace_id, asset_version_id),
  foreign key (workspace_id, run_id, node_id) references public.cs_run_nodes(workspace_id, run_id, node_id),
  foreign key (workspace_id, asset_version_id) references public.cs_asset_versions(workspace_id, id)
);

create table if not exists public.cs_run_admissions (
  workspace_id          uuid not null,
  run_id                uuid not null,
  account_id            uuid not null references public.cs_accounts(id),
  plan_version_id       uuid not null,   -- scoped FK to cs_compile_plans arrives with CS-B2
  plan_sha256           text not null check (plan_sha256 ~ '^[0-9a-f]{64}$'),
  compile_hash          text not null check (compile_hash ~ '^[0-9a-f]{64}$'),
  tier                  public.cs_render_tier not null,
  draft_approval_id     uuid,
  policy_id             uuid not null references public.cs_budget_policies(id),
  funding_source        public.cs_credential_mode not null,
  credential_version_id uuid not null references cs_private.cs_credentials(version_id),
  provider              text not null,
  estimated_cost_micros bigint not null check (estimated_cost_micros >= 0),
  maximum_cost_micros   bigint not null check (maximum_cost_micros >= estimated_cost_micros),
  budget_ceiling_micros bigint not null check (budget_ceiling_micros >= maximum_cost_micros),
  idempotency_key       uuid not null,
  request_sha256        text not null check (request_sha256 ~ '^[0-9a-f]{64}$'),
  admitted_by           uuid not null references auth.users(id),
  admitted_at           timestamptz not null default clock_timestamp(),
  primary key (workspace_id, run_id),
  foreign key (workspace_id, run_id) references public.cs_runs(workspace_id, id),
  foreign key (workspace_id, draft_approval_id) references public.cs_tier_approvals(workspace_id, id),
  check ((tier = 'final') = (draft_approval_id is not null))
);

create table if not exists public.cs_run_node_specs (
  workspace_id          uuid not null,
  run_id                uuid not null,
  node_id               text not null,
  catalogue_version_id  uuid not null references public.cs_provider_catalogue_versions(id),
  dependencies          text[] not null default '{}',
  inputs                jsonb not null check (jsonb_typeof(inputs) = 'array'),
  provider_call         jsonb not null check (jsonb_typeof(provider_call) = 'object'),
  units                 integer not null check (units > 0),
  quote_micros          bigint not null check (quote_micros > 0),
  estimated_cost_micros bigint not null check (estimated_cost_micros >= 0),
  maximum_cost_micros   bigint not null check (maximum_cost_micros >= quote_micros),
  primary key (workspace_id, run_id, node_id),
  foreign key (workspace_id, run_id, node_id) references public.cs_run_nodes(workspace_id, run_id, node_id)
);

-- -----------------------------------------------------------------------------
-- 6. Reservations (mutable projection) and the immutable cost ledger
-- -----------------------------------------------------------------------------
create table if not exists public.cs_budget_reservations (
  id                uuid primary key default gen_random_uuid(),
  workspace_id      uuid not null,
  account_id        uuid not null references public.cs_accounts(id),
  run_id            uuid not null,
  node_id           text not null,
  attempt_id        uuid,
  policy_id         uuid not null references public.cs_budget_policies(id),
  ceiling_micros    bigint not null check (ceiling_micros > 0),
  state             public.cs_reservation_state not null default 'reserved',
  settled_micros    bigint check (settled_micros is null or settled_micros >= 0),
  billing_exception boolean not null default false,
  created_at        timestamptz not null default clock_timestamp(),
  updated_at        timestamptz not null default clock_timestamp(),
  unique (workspace_id, id),
  unique (workspace_id, run_id, node_id),
  foreign key (workspace_id, run_id, node_id) references public.cs_run_nodes(workspace_id, run_id, node_id),
  check ((state in ('settled', 'released')) = (settled_micros is not null)),
  check (billing_exception = false or settled_micros > ceiling_micros)
);
create index if not exists cs_budget_reservations_account_idx on public.cs_budget_reservations(account_id, created_at);

create table if not exists public.cs_cost_ledger (
  id                  uuid primary key default gen_random_uuid(),
  workspace_id        uuid not null,
  account_id          uuid not null references public.cs_accounts(id),
  reservation_id      uuid not null,
  attempt_id          uuid,
  run_id              uuid not null,
  node_id             text not null,
  entry_type          text not null check (entry_type in ('reserve', 'settle', 'release', 'unknown', 'adjust')),
  amount_micros       bigint not null check (amount_micros >= 0),
  provider_units      numeric check (provider_units is null or provider_units >= 0),
  provider_currency   text,
  conversion_snapshot jsonb,
  source_receipt_id   uuid,
  source_event_key    text not null check (length(source_event_key) between 1 and 300),
  created_at          timestamptz not null default clock_timestamp(),
  unique (account_id, source_event_key),
  foreign key (workspace_id, reservation_id) references public.cs_budget_reservations(workspace_id, id),
  foreign key (workspace_id, run_id, node_id) references public.cs_run_nodes(workspace_id, run_id, node_id)
);

-- -----------------------------------------------------------------------------
-- 7. Attempts, attempt events, frozen payloads, provider outputs, receipts
-- -----------------------------------------------------------------------------
create table if not exists public.cs_attempts (
  id                           uuid primary key default gen_random_uuid(),
  workspace_id                 uuid not null,
  run_id                       uuid not null,
  node_id                      text not null,
  attempt_number               integer not null check (attempt_number > 0),
  state                        public.cs_attempt_state not null default 'prepared',
  provider                     text not null,
  provider_account_fingerprint text not null,
  credential_version_id        uuid not null references cs_private.cs_credentials(version_id),
  catalogue_version_id         uuid not null references public.cs_provider_catalogue_versions(id),
  adapter_revision             text not null check (length(adapter_revision) between 1 and 120),
  node_input_sha256            text not null check (node_input_sha256 ~ '^[0-9a-f]{64}$'),
  provider_idempotency_key     uuid not null default gen_random_uuid(),
  budget_reservation_id        uuid not null,
  request_sha256               text check (request_sha256 ~ '^[0-9a-f]{64}$'),
  dispatch_envelope_sha256     text check (dispatch_envelope_sha256 ~ '^[0-9a-f]{64}$'),
  provider_request_ids         text[] not null default '{}',
  replay_count                 integer not null default 0 check (replay_count between 0 and 3),
  dispatched_at                timestamptz,
  error                        jsonb,
  prepared_at                  timestamptz not null default clock_timestamp(),
  updated_at                   timestamptz not null default clock_timestamp(),
  unique (workspace_id, id),
  unique (workspace_id, run_id, node_id, attempt_number),
  unique (provider, provider_account_fingerprint, provider_idempotency_key),
  foreign key (workspace_id, run_id, node_id) references public.cs_run_nodes(workspace_id, run_id, node_id),
  foreign key (workspace_id, budget_reservation_id) references public.cs_budget_reservations(workspace_id, id),
  check (state <> 'prepared' or dispatched_at is null),
  check (state not in ('dispatching', 'submitted', 'waiting_provider', 'ingesting', 'succeeded', 'uncertain')
         or dispatched_at is not null)
);

create table if not exists public.cs_attempt_events (
  workspace_id        uuid not null,
  attempt_id          uuid not null,
  sequence            integer not null check (sequence > 0),
  state               public.cs_attempt_state not null,
  reason_code         text,
  provider_receipt_id uuid,
  occurred_at         timestamptz not null default clock_timestamp(),
  primary key (workspace_id, attempt_id, sequence),
  foreign key (workspace_id, attempt_id) references public.cs_attempts(workspace_id, id)
);

create table if not exists cs_private.cs_attempt_payloads (
  workspace_id    uuid not null,
  attempt_id      uuid not null,
  envelope        jsonb not null check (envelope ->> 'schema_version' = 'cs-dispatch-envelope-v1'),
  envelope_sha256 text not null check (envelope_sha256 ~ '^[0-9a-f]{64}$'),
  dispatch_sha256 text not null check (dispatch_sha256 ~ '^[0-9a-f]{64}$'),
  created_at      timestamptz not null default clock_timestamp(),
  primary key (workspace_id, attempt_id),
  foreign key (workspace_id, attempt_id) references public.cs_attempts(workspace_id, id)
);

-- Provider result locations are protected runtime records (never in events/API).
create table if not exists cs_private.cs_attempt_outputs (
  workspace_id    uuid not null,
  attempt_id      uuid not null,
  ordinal         integer not null check (ordinal between 0 and 39),
  provider_job_id text not null check (length(provider_job_id) between 1 and 300),
  result_url      text not null check (result_url ~ '^https://[^/\s]+/' and length(result_url) <= 4096),
  created_at      timestamptz not null default clock_timestamp(),
  primary key (workspace_id, attempt_id, ordinal),
  foreign key (workspace_id, attempt_id) references public.cs_attempts(workspace_id, id)
);

create table if not exists public.cs_provider_receipts (
  id                           uuid primary key default gen_random_uuid(),
  workspace_id                 uuid not null,
  run_id                       uuid not null,
  node_id                      text not null,
  attempt_id                   uuid not null,
  provider                     text not null,
  provider_account_fingerprint text not null,
  provider_request_id          text check (provider_request_id is null or length(provider_request_id) between 1 and 300),
  kind                         text not null check (kind in ('acceptance', 'status', 'webhook', 'output', 'billing', 'reconciliation')),
  status                       text not null check (status in ('accepted', 'queued', 'running', 'completed', 'failed', 'nsfw', 'cancelled', 'unknown')),
  source                       text not null check (source in ('authenticated_response', 'verified_webhook', 'operator')),
  source_event_key             text not null check (length(source_event_key) between 1 and 300),
  signature_verified           boolean not null default false,
  charge_amount_micros         bigint check (charge_amount_micros is null or charge_amount_micros >= 0),
  charge_evidence              text not null check (charge_evidence in ('provider_billing', 'provider_no_charge', 'operator_reconciliation', 'unknown')),
  charge_certainty             text not null check (charge_certainty in ('provider_reported', 'balance_delta', 'estimated', 'unknown', 'operator_reconciliation', 'not_applicable')),
  billing_reference            text check (billing_reference is null or length(billing_reference) between 1 and 300),
  provider_units               numeric check (provider_units is null or provider_units >= 0),
  provider_unit_name           text,
  conversion_snapshot          jsonb,
  dispatch_envelope_sha256     text not null check (dispatch_envelope_sha256 ~ '^[0-9a-f]{64}$'),
  raw_receipt_sha256           text not null check (raw_receipt_sha256 ~ '^[0-9a-f]{64}$'),
  actor_id                     uuid references auth.users(id),
  note                         text check (note is null or length(note) between 1 and 2000),
  observed_at                  timestamptz not null default clock_timestamp(),
  unique (workspace_id, id),
  unique (provider, provider_account_fingerprint, source_event_key),
  foreign key (workspace_id, attempt_id) references public.cs_attempts(workspace_id, id),
  foreign key (workspace_id, run_id, node_id) references public.cs_run_nodes(workspace_id, run_id, node_id),
  -- unknown evidence never carries an amount; known evidence always does
  check ((charge_evidence = 'unknown') = (charge_amount_micros is null)),
  check ((source = 'operator') = (actor_id is not null and note is not null))
);
create unique index if not exists cs_provider_receipts_acceptance_once
  on public.cs_provider_receipts(provider, provider_account_fingerprint, provider_request_id)
  where kind = 'acceptance';

-- -----------------------------------------------------------------------------
-- 8. Triggers: immutability, attempt write-once/terminal guards, touch
-- -----------------------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array['cs_provider_catalogue_versions', 'cs_budget_policies', 'cs_tier_approvals',
                           'cs_run_admissions', 'cs_run_node_specs', 'cs_cost_ledger', 'cs_attempt_events',
                           'cs_provider_receipts'] loop
    execute format('drop trigger if exists %1$s_immutable on public.%1$s', t);
    execute format('create trigger %1$s_immutable before update or delete on public.%1$s for each row execute function cs_private.cs_reject_mutation()', t);
  end loop;
  foreach t in array array['cs_attempt_payloads', 'cs_attempt_outputs'] loop
    execute format('drop trigger if exists %1$s_immutable on cs_private.%1$s', t);
    execute format('create trigger %1$s_immutable before update or delete on cs_private.%1$s for each row execute function cs_private.cs_reject_mutation()', t);
  end loop;
end $$;

create or replace function cs_private.cs_attempt_guard() returns trigger
language plpgsql set search_path = public, pg_temp as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'cs_immutable_record: attempts cannot be deleted' using errcode = 'CS409';
  end if;
  if (new.id, new.workspace_id, new.run_id, new.node_id, new.attempt_number, new.provider,
      new.provider_account_fingerprint, new.credential_version_id, new.catalogue_version_id, new.adapter_revision,
      new.node_input_sha256, new.provider_idempotency_key, new.budget_reservation_id, new.prepared_at)
     is distinct from
     (old.id, old.workspace_id, old.run_id, old.node_id, old.attempt_number, old.provider,
      old.provider_account_fingerprint, old.credential_version_id, old.catalogue_version_id, old.adapter_revision,
      old.node_input_sha256, old.provider_idempotency_key, old.budget_reservation_id, old.prepared_at) then
    raise exception 'cs_attempt_identity_fixed' using errcode = 'CS409';
  end if;
  if old.state in ('succeeded', 'failed', 'cancelled') and new.state is distinct from old.state then
    raise exception 'cs_attempt_terminal: % cannot become %', old.state, new.state using errcode = 'CS409';
  end if;
  if old.request_sha256 is not null and new.request_sha256 is distinct from old.request_sha256
     or old.dispatch_envelope_sha256 is not null and new.dispatch_envelope_sha256 is distinct from old.dispatch_envelope_sha256
     or old.dispatched_at is not null and new.dispatched_at is distinct from old.dispatched_at then
    raise exception 'cs_attempt_dispatch_frozen' using errcode = 'CS409';
  end if;
  if cardinality(old.provider_request_ids) > 0 and new.provider_request_ids is distinct from old.provider_request_ids then
    raise exception 'cs_attempt_provider_request_id_write_once' using errcode = 'CS409';
  end if;
  new.updated_at := clock_timestamp();
  return new;
end $$;

drop trigger if exists cs_attempts_guard on public.cs_attempts;
create trigger cs_attempts_guard before update or delete on public.cs_attempts
  for each row execute function cs_private.cs_attempt_guard();

create or replace function cs_private.cs_reservation_guard() returns trigger
language plpgsql set search_path = public, pg_temp as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'cs_immutable_record: reservations cannot be deleted' using errcode = 'CS409';
  end if;
  if (new.id, new.workspace_id, new.account_id, new.run_id, new.node_id, new.policy_id, new.ceiling_micros, new.created_at)
     is distinct from (old.id, old.workspace_id, old.account_id, old.run_id, old.node_id, old.policy_id,
                       old.ceiling_micros, old.created_at) then
    raise exception 'cs_reservation_identity_fixed' using errcode = 'CS409';
  end if;
  if old.attempt_id is not null and new.attempt_id is distinct from old.attempt_id then
    raise exception 'cs_reservation_attempt_fixed' using errcode = 'CS409';
  end if;
  if old.state in ('settled', 'released') and (new.state, new.settled_micros) is distinct from (old.state, old.settled_micros) then
    raise exception 'cs_reservation_terminal' using errcode = 'CS409';
  end if;
  new.updated_at := clock_timestamp();
  return new;
end $$;

drop trigger if exists cs_budget_reservations_guard on public.cs_budget_reservations;
create trigger cs_budget_reservations_guard before update or delete on public.cs_budget_reservations
  for each row execute function cs_private.cs_reservation_guard();

-- -----------------------------------------------------------------------------
-- 9. RLS (enabled and forced) + grants. Writes happen only through definer
--    procedures below; runtime roles get SELECT where the contract needs it.
-- -----------------------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array['public.cs_provider_catalogue_versions', 'public.cs_budget_policies',
    'public.cs_tier_approvals', 'public.cs_run_admissions', 'public.cs_run_node_specs',
    'public.cs_budget_reservations', 'public.cs_cost_ledger', 'public.cs_attempts', 'public.cs_attempt_events',
    'public.cs_provider_receipts', 'cs_private.cs_credentials', 'cs_private.cs_attempt_payloads',
    'cs_private.cs_attempt_outputs'] loop
    execute format('alter table %s enable row level security', t);
    execute format('alter table %s force row level security', t);
    execute format('revoke all on table %s from public, anon, authenticated, cs_api, cs_worker', t);
  end loop;
end $$;

do $$
declare r record;
begin
  for r in select schemaname, tablename, policyname from pg_policies
            where policyname like 'cs\_b3\_%'
              and ((schemaname = 'public' and tablename like 'cs\_%') or schemaname = 'cs_private') loop
    execute format('drop policy %I on %I.%I', r.policyname, r.schemaname, r.tablename);
  end loop;
end $$;

-- No runtime-role policies or table grants on CS-B3 tables: cs_api and
-- cs_worker reach them only through the SECURITY DEFINER procedures below,
-- each of which checks actor/workspace/action or a live lease. (This also
-- keeps CS-B3 access intact if the CS-B1 file, whose policy reset covers every
-- cs_* table, is ever replayed.)

-- -----------------------------------------------------------------------------
-- 10. Internal helpers
-- -----------------------------------------------------------------------------
create or replace function cs_private.cs_b3_refuse(p_code text, p_detail text, p_state text default 'CS422')
returns void language plpgsql set search_path = public, pg_temp as $$
begin
  raise exception '%: %', p_code, coalesce(p_detail, '') using errcode = p_state;
end $$;

create or replace function cs_private.cs_set_node_state(
  p_workspace_id uuid, p_run_id uuid, p_node_id text, p_next public.cs_node_state, p_error jsonb default null)
returns void language plpgsql set search_path = public, pg_temp as $$
declare v_prev public.cs_node_state; v_seq bigint;
begin
  select state into v_prev from public.cs_run_nodes
   where workspace_id = p_workspace_id and run_id = p_run_id and node_id = p_node_id for update;
  if v_prev is null or v_prev = p_next then
    return;
  end if;
  v_seq := cs_private.cs_append_run_event(p_workspace_id, p_run_id, 'node.state_changed',
    jsonb_build_object('node_id', p_node_id, 'previous', v_prev, 'next', p_next, 'error', p_error));
  update public.cs_run_nodes set state = p_next, last_event_sequence = v_seq
   where workspace_id = p_workspace_id and run_id = p_run_id and node_id = p_node_id;
end $$;

create or replace function cs_private.cs_attempt_transition(
  p_workspace_id uuid, p_attempt_id uuid, p_next public.cs_attempt_state, p_reason text, p_receipt uuid default null)
returns void language plpgsql set search_path = public, pg_temp as $$
declare v_seq integer;
begin
  update public.cs_attempts set state = p_next
   where workspace_id = p_workspace_id and id = p_attempt_id and state is distinct from p_next;
  select coalesce(max(sequence), 0) + 1 into v_seq from public.cs_attempt_events
   where workspace_id = p_workspace_id and attempt_id = p_attempt_id;
  insert into public.cs_attempt_events (workspace_id, attempt_id, sequence, state, reason_code, provider_receipt_id)
  values (p_workspace_id, p_attempt_id, v_seq, p_next, p_reason, p_receipt);
end $$;

-- Release a reservation that provably incurred no charge (never dispatched).
create or replace function cs_private.cs_release_unspent(p_reservation_id uuid, p_evidence text)
returns void language plpgsql set search_path = public, pg_temp as $$
declare r public.cs_budget_reservations;
begin
  select * into r from public.cs_budget_reservations where id = p_reservation_id for update;
  if r.state <> 'reserved' then
    return;
  end if;
  update public.cs_budget_reservations set state = 'released', settled_micros = 0 where id = r.id;
  insert into public.cs_cost_ledger (workspace_id, account_id, reservation_id, attempt_id, run_id, node_id,
    entry_type, amount_micros, conversion_snapshot, source_event_key)
  values (r.workspace_id, r.account_id, r.id, r.attempt_id, r.run_id, r.node_id, 'release', r.ceiling_micros,
          jsonb_build_object('evidence', p_evidence), 'release:' || r.id);
end $$;

create or replace function cs_private.cs_mark_unknown(p_reservation_id uuid, p_reason text)
returns void language plpgsql set search_path = public, pg_temp as $$
declare r public.cs_budget_reservations;
begin
  select * into r from public.cs_budget_reservations where id = p_reservation_id for update;
  if r.state <> 'reserved' then
    return;
  end if;
  update public.cs_budget_reservations set state = 'unknown' where id = r.id;
  insert into public.cs_cost_ledger (workspace_id, account_id, reservation_id, attempt_id, run_id, node_id,
    entry_type, amount_micros, conversion_snapshot, source_event_key)
  values (r.workspace_id, r.account_id, r.id, r.attempt_id, r.run_id, r.node_id, 'unknown', r.ceiling_micros,
          jsonb_build_object('reason', p_reason), 'unknown:' || r.id);
end $$;

-- Settle with evidenced charge; the unused remainder is released; an overrun
-- is recorded as a billing exception (blocks new spend for the account).
create or replace function cs_private.cs_settle(p_reservation_id uuid, p_amount_micros bigint, p_receipt uuid,
                                                p_units numeric, p_snapshot jsonb, p_key_suffix text)
returns void language plpgsql set search_path = public, pg_temp as $$
declare r public.cs_budget_reservations;
begin
  select * into r from public.cs_budget_reservations where id = p_reservation_id for update;
  if r.state not in ('reserved', 'unknown') then
    perform cs_private.cs_b3_refuse('reservation_already_resolved', r.id::text, 'CS409');
  end if;
  update public.cs_budget_reservations
     set state = 'settled', settled_micros = p_amount_micros, billing_exception = p_amount_micros > r.ceiling_micros
   where id = r.id;
  insert into public.cs_cost_ledger (workspace_id, account_id, reservation_id, attempt_id, run_id, node_id, entry_type,
    amount_micros, provider_units, provider_currency, conversion_snapshot, source_receipt_id, source_event_key)
  values (r.workspace_id, r.account_id, r.id, r.attempt_id, r.run_id, r.node_id, 'settle', p_amount_micros, p_units,
          case when p_units is null then null else 'credits' end, p_snapshot, p_receipt, 'settle:' || r.id || p_key_suffix);
  if p_amount_micros < r.ceiling_micros then
    insert into public.cs_cost_ledger (workspace_id, account_id, reservation_id, attempt_id, run_id, node_id,
      entry_type, amount_micros, conversion_snapshot, source_receipt_id, source_event_key)
    values (r.workspace_id, r.account_id, r.id, r.attempt_id, r.run_id, r.node_id, 'release',
            r.ceiling_micros - p_amount_micros, jsonb_build_object('evidence', 'settled_below_reservation'), p_receipt,
            'release:' || r.id);
  elsif p_amount_micros > r.ceiling_micros then
    insert into public.cs_cost_ledger (workspace_id, account_id, reservation_id, attempt_id, run_id, node_id,
      entry_type, amount_micros, conversion_snapshot, source_receipt_id, source_event_key)
    values (r.workspace_id, r.account_id, r.id, r.attempt_id, r.run_id, r.node_id, 'adjust',
            p_amount_micros - r.ceiling_micros, jsonb_build_object('billing_exception', true), p_receipt,
            'adjust:' || r.id);
  end if;
end $$;

-- Spend counted against a policy in its rolling window: settled charges plus
-- the full ceiling of anything still reserved or unknown (unknown is not zero).
create or replace function cs_private.cs_budget_consumed(p_policy public.cs_budget_policies)
returns bigint language sql stable set search_path = public, pg_temp as $$
  select coalesce(sum(case when r.state in ('reserved', 'unknown') then greatest(r.ceiling_micros, coalesce(r.settled_micros, 0))
                           else r.settled_micros end), 0)::bigint
    from public.cs_budget_reservations r
   where r.account_id = p_policy.account_id
     and (p_policy.workspace_id is null or r.workspace_id = p_policy.workspace_id)
     and (r.created_at > clock_timestamp() - make_interval(secs => p_policy.window_seconds)
          or r.state in ('reserved', 'unknown'));
$$;

create or replace function cs_private.cs_current_policy(p_account_id uuid, p_workspace_id uuid)
returns public.cs_budget_policies language sql stable set search_path = public, pg_temp as $$
  select p.* from public.cs_budget_policies p
   where p.account_id = p_account_id and (p.workspace_id = p_workspace_id or p.workspace_id is null)
     and p.effective_at <= clock_timestamp()
   order by (p.workspace_id is not null) desc, p.revision desc
   limit 1;
$$;

create or replace function cs_private.cs_run_cost(p_workspace_id uuid, p_run_id uuid)
returns jsonb language sql stable set search_path = public, pg_temp as $$
  select jsonb_build_object(
    'currency', 'USD',
    'state', case when bool_or(r.state = 'unknown') then 'unknown'
                  when bool_or(r.state = 'reserved') then 'reserved'
                  else 'settled' end,
    'estimated_micros', coalesce((select a.estimated_cost_micros from public.cs_run_admissions a
                                   where a.workspace_id = p_workspace_id and a.run_id = p_run_id), 0),
    'reserved_micros', coalesce(sum(r.ceiling_micros), 0),
    'settled_micros', coalesce(sum(r.settled_micros), 0),
    'unresolved_micros', coalesce(sum(r.ceiling_micros) filter (where r.state in ('reserved', 'unknown')), 0),
    'receipt_ids', coalesce((select jsonb_agg(pr.id order by pr.observed_at, pr.id) from public.cs_provider_receipts pr
                              where pr.workspace_id = p_workspace_id and pr.run_id = p_run_id), '[]'::jsonb))
    from public.cs_budget_reservations r
   where r.workspace_id = p_workspace_id and r.run_id = p_run_id;
$$;

-- Advance the run DAG from durable node state. Idempotent; called inside every
-- procedure that changes node state and by the recovery sweep.
create or replace function cs_private.cs_advance_run(p_workspace_id uuid, p_run_id uuid)
returns public.cs_run_state language plpgsql set search_path = public, pg_temp as $$
declare
  v_run public.cs_runs;
  n record;
  v_next public.cs_run_state;
  v_inflight boolean;
  v_recon boolean;
  v_cost jsonb;
begin
  select * into v_run from public.cs_runs where workspace_id = p_workspace_id and id = p_run_id for update;
  if v_run.state in ('completed', 'failed', 'cancelled') then
    return v_run.state;
  end if;
  -- pending nodes: release when every dependency succeeded; cancel when a
  -- dependency can no longer succeed or the run is being cancelled.
  for n in select rn.node_id, s.dependencies
             from public.cs_run_nodes rn
             join public.cs_run_node_specs s using (workspace_id, run_id, node_id)
            where rn.workspace_id = p_workspace_id and rn.run_id = p_run_id and rn.state = 'pending'
            order by rn.node_id loop
    if v_run.cancel_requested_at is not null or exists (
         select 1 from public.cs_run_nodes d
          where d.workspace_id = p_workspace_id and d.run_id = p_run_id and d.node_id = any(n.dependencies)
            and d.state in ('failed', 'cancelled', 'skipped', 'authorization_blocked', 'budget_blocked')) then
      perform cs_private.cs_set_node_state(p_workspace_id, p_run_id, n.node_id, 'cancelled',
        jsonb_build_object('code', case when v_run.cancel_requested_at is not null then 'run_cancelled'
                                        else 'dependency_unavailable' end));
      perform cs_private.cs_release_unspent(r.id, 'not_dispatched')
         from public.cs_budget_reservations r
        where r.workspace_id = p_workspace_id and r.run_id = p_run_id and r.node_id = n.node_id;
    elsif not exists (
         select 1 from public.cs_run_nodes d
          where d.workspace_id = p_workspace_id and d.run_id = p_run_id and d.node_id = any(n.dependencies)
            and d.state <> 'succeeded') then
      perform cs_private.cs_set_node_state(p_workspace_id, p_run_id, n.node_id, 'queued');
      insert into public.cs_work_items (workspace_id, environment_id, run_id, node_id, stage, dedupe_key, max_attempts)
      values (p_workspace_id, v_run.environment_id, p_run_id, n.node_id, 'provider_submit',
              'submit:' || p_run_id || ':' || n.node_id, 3)
      on conflict (environment_id, dedupe_key) do nothing;
    end if;
  end loop;

  select exists (select 1 from public.cs_run_nodes where workspace_id = p_workspace_id and run_id = p_run_id
                  and state in ('queued', 'running', 'waiting_provider'))
    into v_inflight;
  select exists (select 1 from public.cs_run_nodes where workspace_id = p_workspace_id and run_id = p_run_id
                  and state = 'needs_reconciliation')
      or exists (select 1 from public.cs_budget_reservations where workspace_id = p_workspace_id and run_id = p_run_id
                  and billing_exception)
    into v_recon;

  v_next := case
    when v_inflight then case when v_run.cancel_requested_at is not null then 'cancel_requested'::public.cs_run_state
                              when v_run.state = 'queued' then 'queued' else 'running' end
    when v_recon then 'needs_reconciliation'
    when v_run.cancel_requested_at is not null then 'cancelled'
    when not exists (select 1 from public.cs_run_nodes where workspace_id = p_workspace_id and run_id = p_run_id
                      and state <> 'succeeded') then 'completed'
    when exists (select 1 from public.cs_run_nodes where workspace_id = p_workspace_id and run_id = p_run_id
                  and state = 'authorization_blocked') then 'authorization_blocked'
    else 'failed' end;

  v_cost := cs_private.cs_run_cost(p_workspace_id, p_run_id);
  if v_next is distinct from v_run.state then
    perform cs_private.cs_append_run_event(p_workspace_id, p_run_id, 'run.state_changed',
      jsonb_build_object('previous', v_run.state, 'next', v_next, 'error', null));
  end if;
  update public.cs_runs set state = v_next, cost_state = (v_cost ->> 'state')::public.cs_cost_state,
         failure_code = case when v_next in ('failed', 'needs_reconciliation', 'authorization_blocked')
                             then coalesce(failure_code, v_next::text) else failure_code end
   where workspace_id = p_workspace_id and id = p_run_id;
  return v_next;
end $$;

-- -----------------------------------------------------------------------------
-- 11. Catalogue publication (operator / deployment only: no runtime grant)
-- -----------------------------------------------------------------------------
create or replace function public.cs_publish_catalogue_entry(
  p_provider text, p_entry_id text, p_model_id text, p_endpoint_key text, p_surface text, p_operation text,
  p_tier public.cs_render_tier, p_enabled boolean, p_credential_modes public.cs_credential_mode[], p_entry jsonb,
  p_adapter_config jsonb, p_price_snapshot jsonb, p_unit_cost_micros bigint, p_max_units integer, p_valid_until timestamptz,
  p_price_expires_at timestamptz, p_sha256 text)
returns uuid language plpgsql security definer set search_path = public, pg_temp as $$
declare v_id uuid;
begin
  insert into public.cs_provider_catalogue_versions (provider, entry_id, model_id, endpoint_key, surface, operation,
    tier, enabled, credential_modes, entry, adapter_config, price_snapshot, credit_price_micros, unit_cost_micros,
    max_units_per_request, maximum_request_cost_micros, price_expires_at, valid_until, sha256)
  values (p_provider, p_entry_id, p_model_id, p_endpoint_key, p_surface, p_operation, p_tier, p_enabled,
          p_credential_modes, p_entry, p_adapter_config, p_price_snapshot, (p_price_snapshot ->> 'credit_price_micros')::bigint,
          p_unit_cost_micros, p_max_units, p_unit_cost_micros * p_max_units, p_price_expires_at, p_valid_until, p_sha256)
  on conflict (provider, entry_id, sha256) do nothing
  returning id into v_id;
  if v_id is null then
    select id into v_id from public.cs_provider_catalogue_versions
     where provider = p_provider and entry_id = p_entry_id and sha256 = p_sha256;
  end if;
  return v_id;
end $$;

-- -----------------------------------------------------------------------------
-- 12. API procedures (cs_api). Actor comes only from verified request claims.
-- -----------------------------------------------------------------------------
create or replace function public.cs_set_budget_policy(
  p_account_id uuid, p_workspace_id uuid, p_funding_source public.cs_credential_mode, p_window_seconds integer,
  p_ceiling_micros bigint, p_per_run_ceiling_micros bigint, p_expires_at timestamptz,
  p_idempotency_key uuid, p_request_sha256 text)
returns table (policy_id uuid, revision integer, replayed boolean)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_actor uuid := cs_private.cs_require_actor();
  v_prior jsonb;
  v_rev integer;
  v_id uuid;
begin
  if not cs_private.cs_account_admin(p_account_id) then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  v_prior := cs_private.cs_idempotency_replay(null, 'budget.set', p_idempotency_key, p_request_sha256);
  if v_prior is not null then
    return query select (v_prior ->> 'policy_id')::uuid, (v_prior ->> 'revision')::integer, true;
    return;
  end if;
  if p_workspace_id is not null and not exists (select 1 from public.cs_workspaces w
                                                 where w.id = p_workspace_id and w.account_id = p_account_id) then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  if p_window_seconds is null or p_window_seconds <= 0 or p_ceiling_micros is null or p_ceiling_micros < 0
     or p_per_run_ceiling_micros is null or p_per_run_ceiling_micros < 0
     or p_per_run_ceiling_micros > p_ceiling_micros then
    perform cs_private.cs_b3_refuse('invalid_budget_policy', 'window > 0, 0 <= per_run <= ceiling', 'CS422');
  end if;
  perform pg_advisory_xact_lock(hashtextextended('cs_budget:' || p_account_id, 0));
  select coalesce(max(p.revision), 0) + 1 into v_rev from public.cs_budget_policies p
   where p.account_id = p_account_id and p.workspace_id is not distinct from p_workspace_id;
  insert into public.cs_budget_policies (account_id, workspace_id, revision, funding_source, window_seconds,
    ceiling_micros, per_run_ceiling_micros, authorization_ref, approved_by, expires_at)
  values (p_account_id, p_workspace_id, v_rev, p_funding_source, p_window_seconds, p_ceiling_micros,
          p_per_run_ceiling_micros,
          jsonb_build_object('kind', 'account_admin_approval', 'actor_id', v_actor, 'request_sha256', p_request_sha256),
          v_actor, p_expires_at)
  returning id into v_id;
  insert into public.cs_idempotency (workspace_id, actor_id, operation, key, request_sha256, result_ref)
  values (null, v_actor, 'budget.set', p_idempotency_key, p_request_sha256,
          jsonb_build_object('policy_id', v_id, 'revision', v_rev));
  insert into public.cs_audit_events (account_id, workspace_id, actor_id, action, input_sha256, affected_refs)
  values (p_account_id, p_workspace_id, v_actor, 'budget.set', p_request_sha256,
          jsonb_build_array(jsonb_build_object('kind', 'budget_policy', 'id', v_id, 'revision', v_rev)));
  return query select v_id, v_rev, false;
end $$;

create or replace function public.cs_store_workspace_credential(
  p_workspace_id uuid, p_environment_id text, p_provider text, p_fingerprint text, p_ciphertext bytea, p_nonce bytea,
  p_key_version text)
returns uuid language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_require_actor();
  v_id uuid;
  v_version uuid;
begin
  if not cs_private.cs_can(p_workspace_id, 'read') then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  if not cs_private.cs_can(p_workspace_id, 'manage') then
    perform cs_private.cs_b3_refuse('capability_denied', null, 'CS403');
  end if;
  select c.id into v_id from cs_private.cs_credentials c
   where c.workspace_id = p_workspace_id and c.provider = p_provider order by c.created_at desc limit 1;
  update cs_private.cs_credentials c set status = 'revoked', revoked_at = clock_timestamp()
   where c.workspace_id = p_workspace_id and c.provider = p_provider and c.status = 'active';
  insert into cs_private.cs_credentials (id, workspace_id, environment_id, provider, mode, provider_account_fingerprint,
    ciphertext, nonce, encryption_key_version, created_by)
  values (coalesce(v_id, gen_random_uuid()), p_workspace_id, p_environment_id, p_provider, 'workspace', p_fingerprint,
          p_ciphertext, p_nonce, p_key_version, v_actor)
  returning version_id into v_version;
  insert into public.cs_audit_events (account_id, workspace_id, actor_id, action, affected_refs)
  select w.account_id, p_workspace_id, v_actor, 'credential.store',
         jsonb_build_array(jsonb_build_object('kind', 'credential_version', 'id', v_version,
                                              'fingerprint', p_fingerprint))
    from public.cs_workspaces w where w.id = p_workspace_id;
  return v_version;
end $$;

create or replace function public.cs_revoke_workspace_credential(p_workspace_id uuid, p_provider text)
returns integer language plpgsql security definer set search_path = public, pg_temp as $$
declare v_actor uuid := cs_private.cs_require_actor(); v_n integer;
begin
  if not cs_private.cs_can(p_workspace_id, 'read') then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  if not cs_private.cs_can(p_workspace_id, 'manage') then
    perform cs_private.cs_b3_refuse('capability_denied', null, 'CS403');
  end if;
  update cs_private.cs_credentials c set status = 'revoked', revoked_at = clock_timestamp()
   where c.workspace_id = p_workspace_id and c.provider = p_provider and c.status = 'active';
  get diagnostics v_n = row_count;
  insert into public.cs_audit_events (account_id, workspace_id, actor_id, action, affected_refs)
  select w.account_id, p_workspace_id, v_actor, 'credential.revoke',
         jsonb_build_array(jsonb_build_object('kind', 'credential', 'provider', p_provider))
    from public.cs_workspaces w where w.id = p_workspace_id;
  return v_n;
end $$;

-- Quote a frozen plan's billable nodes against the current catalogue.
-- p_nodes: [{node_id, dependencies[], inputs[], provider_call{}, estimated_cost_micros, maximum_cost_micros}]
create or replace function cs_private.cs_quote_nodes(p_workspace_id uuid, p_nodes jsonb, p_tier public.cs_render_tier)
returns jsonb language plpgsql stable set search_path = public, pg_temp as $$
declare
  n jsonb;
  c public.cs_provider_catalogue_versions;
  v_units integer;
  v_quote bigint;
  v_out jsonb := '[]'::jsonb;
  v_est bigint := 0;
  v_max bigint := 0;
  v_ids text[];
  v_provider text;
begin
  if p_nodes is null or jsonb_typeof(p_nodes) <> 'array' or jsonb_array_length(p_nodes) = 0
     or jsonb_array_length(p_nodes) > 200 then
    perform cs_private.cs_b3_refuse('plan_invalid', 'nodes must be a non-empty array (<=200)');
  end if;
  select array_agg(x ->> 'node_id') into v_ids from jsonb_array_elements(p_nodes) x;
  if exists (select 1 from unnest(v_ids) i where i is null or i !~ '^[a-z][a-z0-9_-]{0,63}$')
     or cardinality(v_ids) <> (select count(distinct i) from unnest(v_ids) i) then
    perform cs_private.cs_b3_refuse('plan_invalid', 'node ids must be unique slugs');
  end if;
  -- dependencies exist and the graph is acyclic
  if exists (select 1 from jsonb_array_elements(p_nodes) x, jsonb_array_elements_text(coalesce(x -> 'dependencies', '[]')) d
              where d <> all(v_ids) or d = x ->> 'node_id') then
    perform cs_private.cs_b3_refuse('plan_invalid', 'unknown or self dependency');
  end if;
  if exists (
    with recursive edges(src, dst) as (
      select x ->> 'node_id', d from jsonb_array_elements(p_nodes) x,
             jsonb_array_elements_text(coalesce(x -> 'dependencies', '[]')) d),
    walk(start, cur, depth) as (
      select src, dst, 1 from edges
      union all
      select w.start, e.dst, w.depth + 1 from walk w join edges e on e.src = w.cur
       where w.depth <= cardinality(v_ids) and w.cur <> w.start)
    select 1 from walk where cur = start) then
    perform cs_private.cs_b3_refuse('plan_invalid', 'dependency cycle');
  end if;

  for n in select * from jsonb_array_elements(p_nodes) loop
    if jsonb_typeof(n -> 'provider_call') is distinct from 'object' then
      perform cs_private.cs_b3_refuse('render_stage_unavailable',
        format('node %s has no provider call; local render stages are not admitted by this runtime', n ->> 'node_id'));
    end if;
    if v_provider is not null and v_provider <> n #>> '{provider_call,provider}' then
      perform cs_private.cs_b3_refuse('multi_provider_unsupported', 'one provider credential per run');
    end if;
    v_provider := n #>> '{provider_call,provider}';
    select * into c from public.cs_provider_catalogue_versions cv
     where cv.provider = n #>> '{provider_call,provider}'
       and cv.entry_id = n #>> '{provider_call,catalogue_entry_id}'
     order by cv.published_at desc, cv.id limit 1;
    if c.id is null or not c.enabled or c.valid_until <= clock_timestamp() or c.price_expires_at <= clock_timestamp() then
      perform cs_private.cs_b3_refuse('price_unknown',
        format('node %s: no current enabled priced catalogue entry %s', n ->> 'node_id', n #>> '{provider_call,catalogue_entry_id}'));
    end if;
    if c.model_id <> n #>> '{provider_call,model_id}' or c.endpoint_key <> n #>> '{provider_call,endpoint_key}' then
      perform cs_private.cs_b3_refuse('plan_stale', format('node %s does not match catalogue entry %s', n ->> 'node_id', c.entry_id));
    end if;
    if c.price_snapshot -> 'quote_parameters' ? 'quality'
       and (n #>> '{provider_call,parameters,quality}') is distinct from (c.price_snapshot #>> '{quote_parameters,quality}') then
      perform cs_private.cs_b3_refuse('plan_stale', format('node %s quality does not match catalogue entry %s',
        n ->> 'node_id', c.entry_id));
    end if;
    if c.tier <> p_tier then
      perform cs_private.cs_b3_refuse('tier_mismatch', format('node %s uses a %s entry in a %s run', n ->> 'node_id', c.tier, p_tier));
    end if;
    v_units := coalesce((n #>> '{provider_call,parameters,batch_size}')::integer, 1);
    if v_units < 1 or v_units > c.max_units_per_request then
      perform cs_private.cs_b3_refuse('units_exceed_catalogue', format('node %s: %s units, catalogue allows %s',
        n ->> 'node_id', v_units, c.max_units_per_request));
    end if;
    v_quote := c.unit_cost_micros * v_units;
    if (n ->> 'maximum_cost_micros')::bigint is null or (n ->> 'maximum_cost_micros')::bigint < v_quote
       or (n ->> 'estimated_cost_micros')::bigint is null
       or (n ->> 'estimated_cost_micros')::bigint > (n ->> 'maximum_cost_micros')::bigint then
      perform cs_private.cs_b3_refuse('plan_cost_below_quote', format('node %s: plan maximum %s < current quote %s',
        n ->> 'node_id', n ->> 'maximum_cost_micros', v_quote));
    end if;
    v_est := v_est + greatest((n ->> 'estimated_cost_micros')::bigint, v_quote);
    v_max := v_max + (n ->> 'maximum_cost_micros')::bigint;
    v_out := v_out || jsonb_build_array(jsonb_build_object(
      'node_id', n ->> 'node_id', 'catalogue_version_id', c.id, 'entry_id', c.entry_id, 'tier', c.tier,
      'units', v_units, 'unit_cost_micros', c.unit_cost_micros, 'quote_micros', v_quote,
      'estimated_cost_micros', greatest((n ->> 'estimated_cost_micros')::bigint, v_quote),
      'maximum_cost_micros', (n ->> 'maximum_cost_micros')::bigint,
      'credential_modes', to_jsonb(c.credential_modes), 'price_snapshot', c.price_snapshot));
  end loop;
  return jsonb_build_object('provider', v_provider, 'nodes', v_out, 'estimated_micros', v_est, 'maximum_micros', v_max,
                            'currency', 'USD');
end $$;

create or replace function public.cs_quote_run(p_workspace_id uuid, p_nodes jsonb, p_tier public.cs_render_tier)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_quote jsonb;
  v_account uuid;
  v_policy public.cs_budget_policies;
begin
  perform cs_private.cs_require_actor();
  if not cs_private.cs_can(p_workspace_id, 'read') then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  if not cs_private.cs_can(p_workspace_id, 'produce') then
    perform cs_private.cs_b3_refuse('capability_denied', null, 'CS403');
  end if;
  v_quote := cs_private.cs_quote_nodes(p_workspace_id, p_nodes, p_tier);
  select account_id into v_account from public.cs_workspaces where id = p_workspace_id;
  v_policy := cs_private.cs_current_policy(v_account, p_workspace_id);
  return v_quote || jsonb_build_object('budget', case when v_policy.id is null then null else jsonb_build_object(
    'policy_id', v_policy.id, 'revision', v_policy.revision, 'ceiling_micros', v_policy.ceiling_micros,
    'per_run_ceiling_micros', v_policy.per_run_ceiling_micros,
    'available_micros', greatest(v_policy.ceiling_micros - cs_private.cs_budget_consumed(v_policy), 0)) end);
end $$;

create or replace function public.cs_admit_run(
  p_workspace_id uuid, p_environment_id text, p_release_id text, p_plan_version_id uuid, p_plan_sha256 text,
  p_compile_hash text, p_budget_ceiling_micros bigint, p_nodes jsonb, p_tier public.cs_render_tier,
  p_draft_approval_id uuid, p_idempotency_key uuid, p_request_sha256 text)
returns table (run_id uuid, state public.cs_run_state, reserved_micros bigint, replayed boolean)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_actor uuid := cs_private.cs_require_actor();
  v_prior jsonb;
  v_limits cs_private.cs_runtime_limits;
  v_account uuid;
  v_policy public.cs_budget_policies;
  v_quote jsonb;
  v_max bigint;
  v_consumed bigint;
  v_cred cs_private.cs_credentials;
  v_approval public.cs_tier_approvals;
  v_run uuid := gen_random_uuid();
  q jsonb;
  n jsonb;
  v_res uuid;
  v_res_ids jsonb := '[]'::jsonb;
  v_active integer;
begin
  if not cs_private.cs_can(p_workspace_id, 'read') then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  if not cs_private.cs_can(p_workspace_id, 'produce') then
    perform cs_private.cs_b3_refuse('capability_denied', null, 'CS403');
  end if;
  v_prior := cs_private.cs_idempotency_replay(p_workspace_id, 'run.create', p_idempotency_key, p_request_sha256);
  if v_prior is not null then
    return query select r.id, r.state, (v_prior ->> 'reserved_micros')::bigint, true
      from public.cs_runs r where r.workspace_id = p_workspace_id and r.id = (v_prior ->> 'run_id')::uuid;
    return;
  end if;
  if p_plan_sha256 !~ '^[0-9a-f]{64}$' or p_compile_hash !~ '^[0-9a-f]{64}$' or p_plan_version_id is null then
    perform cs_private.cs_b3_refuse('plan_invalid', 'plan version, sha256 and compile hash are required');
  end if;
  select * into v_limits from cs_private.cs_runtime_limits l where l.environment_id = p_environment_id;
  if v_limits.environment_id is null then
    perform cs_private.cs_b3_refuse('environment_unavailable', null, 'CS503');
  end if;
  if not v_limits.admission_enabled then
    perform cs_private.cs_b3_refuse('admission_disabled', 'paid admission is switched off for this environment', 'CS503');
  end if;
  select w.account_id into v_account from public.cs_workspaces w where w.id = p_workspace_id;

  -- account budget lock: serialises every admission/settlement decision for the account
  perform pg_advisory_xact_lock(hashtextextended('cs_budget:' || v_account, 0));

  if exists (select 1 from public.cs_budget_reservations r where r.account_id = v_account and r.billing_exception) then
    perform cs_private.cs_b3_refuse('billing_exception_open',
      'a provider charge exceeded its reservation; an operator must reconcile before new spend', 'CS409');
  end if;
  select count(*) into v_active from public.cs_runs r
   where r.workspace_id = p_workspace_id and r.state in ('queued', 'running', 'waiting_provider', 'cancel_requested');
  if v_active >= v_limits.active_run_limit then
    perform cs_private.cs_b3_refuse('active_run_limit_reached', format('%s active runs', v_active), 'CS409');
  end if;

  v_quote := cs_private.cs_quote_nodes(p_workspace_id, p_nodes, p_tier);
  v_max := (v_quote ->> 'maximum_micros')::bigint;

  -- draft -> final: a final run must use an approved draft output as a reference
  if p_tier = 'final' then
    if p_draft_approval_id is null then
      perform cs_private.cs_b3_refuse('draft_approval_required', 'final renders need an approved draft');
    end if;
    select * into v_approval from public.cs_tier_approvals a
     where a.workspace_id = p_workspace_id and a.id = p_draft_approval_id;
    if v_approval.id is null then
      perform cs_private.cs_b3_refuse('draft_approval_required', 'approval not found in this workspace');
    end if;
    for n in select * from jsonb_array_elements(p_nodes) loop
      if not exists (select 1 from jsonb_array_elements(coalesce(n -> 'inputs', '[]')) i
                      where i #>> '{source,kind}' = 'asset'
                        and i #>> '{source,asset,asset_version_id}' = v_approval.asset_version_id::text
                        and i #>> '{source,asset,sha256}' = v_approval.sha256) then
        perform cs_private.cs_b3_refuse('draft_reference_required',
          format('final node %s does not reference the approved draft', n ->> 'node_id'));
      end if;
    end loop;
  elsif p_draft_approval_id is not null then
    perform cs_private.cs_b3_refuse('plan_invalid', 'draft runs do not take a draft approval');
  end if;
  -- every asset input must be an existing version in this workspace with the same digest
  perform cs_private.cs_assert_asset_refs(p_workspace_id,
    coalesce((select jsonb_agg(i #> '{source,asset}') from jsonb_array_elements(p_nodes) x,
               jsonb_array_elements(coalesce(x -> 'inputs', '[]')) i where i #>> '{source,kind}' = 'asset'), '[]'::jsonb));

  -- budget
  v_policy := cs_private.cs_current_policy(v_account, p_workspace_id);
  if v_policy.id is null or v_policy.ceiling_micros = 0 or v_policy.per_run_ceiling_micros = 0 then
    perform cs_private.cs_b3_refuse('budget_not_approved', 'no owner-approved budget for this workspace');
  end if;
  if v_policy.expires_at is not null and v_policy.expires_at <= clock_timestamp() then
    perform cs_private.cs_b3_refuse('budget_policy_expired', v_policy.id::text);
  end if;
  if p_budget_ceiling_micros is null or v_max > p_budget_ceiling_micros then
    perform cs_private.cs_b3_refuse('plan_ceiling_exceeded',
      format('maximum %s exceeds the plan budget ceiling %s', v_max, p_budget_ceiling_micros));
  end if;
  if v_max > v_policy.per_run_ceiling_micros then
    perform cs_private.cs_b3_refuse('budget_exceeded',
      format('run maximum %s exceeds the per-run ceiling %s', v_max, v_policy.per_run_ceiling_micros));
  end if;
  v_consumed := cs_private.cs_budget_consumed(v_policy);
  if v_consumed + v_max > v_policy.ceiling_micros then
    perform cs_private.cs_b3_refuse('budget_exceeded',
      format('run maximum %s exceeds available %s of ceiling %s', v_max,
             greatest(v_policy.ceiling_micros - v_consumed, 0), v_policy.ceiling_micros));
  end if;

  -- credential for the policy's funding source
  for q in select * from jsonb_array_elements(v_quote -> 'nodes') loop
    if not (q -> 'credential_modes') ? v_policy.funding_source::text then
      perform cs_private.cs_b3_refuse('credential_mode_unsupported',
        format('entry %s does not allow %s funding', q ->> 'entry_id', v_policy.funding_source));
    end if;
  end loop;
  select * into v_cred from cs_private.cs_credentials c
   where c.provider = v_quote ->> 'provider' and c.status = 'active' and c.mode = v_policy.funding_source
     and c.environment_id = p_environment_id
     and (c.workspace_id = p_workspace_id or (v_policy.funding_source = 'platform' and c.workspace_id is null))
   order by c.created_at desc limit 1;
  if v_cred.version_id is null then
    perform cs_private.cs_b3_refuse('provider_credentials_missing',
      format('no active %s credential for %s', v_policy.funding_source, v_quote ->> 'provider'));
  end if;

  -- durable records, one transaction
  insert into public.cs_runs (id, workspace_id, environment_id, plan_version_id, requested_by, state,
    authorization_receipt, cost_state, release_id)
  values (v_run, p_workspace_id, p_environment_id, p_plan_version_id, v_actor, 'queued',
          jsonb_build_object('policy_id', v_policy.id, 'policy_revision', v_policy.revision, 'tier', p_tier,
                             'funding_source', v_policy.funding_source, 'maximum_cost_micros', v_max),
          'reserved', p_release_id);
  for n in select * from jsonb_array_elements(p_nodes) loop
    select x into q from jsonb_array_elements(v_quote -> 'nodes') x where x ->> 'node_id' = n ->> 'node_id';
    insert into public.cs_run_nodes (workspace_id, run_id, node_id, state)
    values (p_workspace_id, v_run, n ->> 'node_id',
            (case when jsonb_array_length(coalesce(n -> 'dependencies', '[]')) = 0 then 'queued' else 'pending' end)::public.cs_node_state);
    insert into public.cs_run_node_specs (workspace_id, run_id, node_id, catalogue_version_id, dependencies, inputs,
      provider_call, units, quote_micros, estimated_cost_micros, maximum_cost_micros)
    values (p_workspace_id, v_run, n ->> 'node_id', (q ->> 'catalogue_version_id')::uuid,
            array(select jsonb_array_elements_text(coalesce(n -> 'dependencies', '[]'))),
            coalesce(n -> 'inputs', '[]'), n -> 'provider_call', (q ->> 'units')::integer,
            (q ->> 'quote_micros')::bigint, (q ->> 'estimated_cost_micros')::bigint, (q ->> 'maximum_cost_micros')::bigint);
    insert into public.cs_budget_reservations (workspace_id, account_id, run_id, node_id, policy_id, ceiling_micros)
    values (p_workspace_id, v_account, v_run, n ->> 'node_id', v_policy.id, (q ->> 'maximum_cost_micros')::bigint)
    returning id into v_res;
    insert into public.cs_cost_ledger (workspace_id, account_id, reservation_id, run_id, node_id, entry_type,
      amount_micros, conversion_snapshot, source_event_key)
    values (p_workspace_id, v_account, v_res, v_run, n ->> 'node_id', 'reserve', (q ->> 'maximum_cost_micros')::bigint,
            jsonb_build_object('quote_micros', (q ->> 'quote_micros')::bigint, 'units', (q ->> 'units')::integer,
                               'unit_cost_micros', (q ->> 'unit_cost_micros')::bigint,
                               'catalogue_version_id', q ->> 'catalogue_version_id'),
            'reserve:' || v_res);
    v_res_ids := v_res_ids || to_jsonb(v_res);
    if jsonb_array_length(coalesce(n -> 'dependencies', '[]')) = 0 then
      insert into public.cs_work_items (workspace_id, environment_id, run_id, node_id, stage, dedupe_key, max_attempts)
      values (p_workspace_id, p_environment_id, v_run, n ->> 'node_id', 'provider_submit',
              'submit:' || v_run || ':' || (n ->> 'node_id'), 3);
    end if;
  end loop;
  insert into public.cs_run_admissions (workspace_id, run_id, account_id, plan_version_id, plan_sha256, compile_hash,
    tier, draft_approval_id, policy_id, funding_source, credential_version_id, provider, estimated_cost_micros,
    maximum_cost_micros, budget_ceiling_micros, idempotency_key, request_sha256, admitted_by)
  values (p_workspace_id, v_run, v_account, p_plan_version_id, p_plan_sha256, p_compile_hash, p_tier,
          p_draft_approval_id, v_policy.id, v_policy.funding_source, v_cred.version_id, v_quote ->> 'provider',
          (v_quote ->> 'estimated_micros')::bigint, v_max, p_budget_ceiling_micros, p_idempotency_key,
          p_request_sha256, v_actor);
  perform cs_private.cs_append_run_event(p_workspace_id, v_run, 'run.admitted',
    jsonb_build_object('plan', jsonb_build_object('id', p_plan_version_id, 'sha256', p_plan_sha256),
                       'compile_hash', p_compile_hash, 'reservation_ids', v_res_ids, 'reserved_micros', v_max,
                       'tier', p_tier));
  insert into public.cs_idempotency (workspace_id, actor_id, operation, key, request_sha256, result_ref)
  values (p_workspace_id, v_actor, 'run.create', p_idempotency_key, p_request_sha256,
          jsonb_build_object('run_id', v_run, 'reserved_micros', v_max));
  insert into public.cs_audit_events (account_id, workspace_id, actor_id, action, input_sha256, affected_refs)
  values (v_account, p_workspace_id, v_actor, 'run.admit', p_request_sha256,
          jsonb_build_array(jsonb_build_object('kind', 'run', 'id', v_run, 'reserved_micros', v_max)));
  return query select v_run, 'queued'::public.cs_run_state, v_max, false;
end $$;

create or replace function public.cs_approve_draft(
  p_workspace_id uuid, p_run_id uuid, p_node_id text, p_asset_version_id uuid, p_rationale text)
returns uuid language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_require_actor();
  v_version public.cs_asset_versions;
  v_id uuid;
begin
  if not cs_private.cs_can(p_workspace_id, 'read') then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  if not cs_private.cs_can(p_workspace_id, 'review') then
    perform cs_private.cs_b3_refuse('capability_denied', null, 'CS403');
  end if;
  if not exists (select 1 from public.cs_run_admissions a join public.cs_run_nodes rn
                   on rn.workspace_id = a.workspace_id and rn.run_id = a.run_id
                  where a.workspace_id = p_workspace_id and a.run_id = p_run_id and a.tier = 'draft'
                    and rn.node_id = p_node_id and rn.state = 'succeeded') then
    perform cs_private.cs_b3_refuse('draft_not_approvable', 'only a succeeded node of a draft run can be approved');
  end if;
  select * into v_version from public.cs_asset_versions v
   where v.workspace_id = p_workspace_id and v.id = p_asset_version_id and v.run_id = p_run_id and v.node_id = p_node_id;
  if v_version.id is null then
    perform cs_private.cs_b3_refuse('draft_not_approvable', 'asset version is not an output of that node');
  end if;
  insert into public.cs_tier_approvals (workspace_id, run_id, node_id, asset_id, asset_version_id, sha256, approved_by, rationale)
  values (p_workspace_id, p_run_id, p_node_id, v_version.asset_id, v_version.id, v_version.sha256, v_actor,
          nullif(btrim(coalesce(p_rationale, '')), ''))
  on conflict (workspace_id, asset_version_id) do nothing
  returning id into v_id;
  if v_id is null then
    select id into v_id from public.cs_tier_approvals where workspace_id = p_workspace_id and asset_version_id = p_asset_version_id;
    return v_id;
  end if;
  insert into public.cs_audit_events (account_id, workspace_id, actor_id, action, affected_refs)
  select w.account_id, p_workspace_id, v_actor, 'draft.approve',
         jsonb_build_array(jsonb_build_object('kind', 'tier_approval', 'id', v_id),
                           jsonb_build_object('kind', 'asset_version', 'id', p_asset_version_id))
    from public.cs_workspaces w where w.id = p_workspace_id;
  return v_id;
end $$;

create or replace function public.cs_request_cancel(p_workspace_id uuid, p_run_id uuid, p_reason text)
returns public.cs_run_state language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_require_actor();
  v_run public.cs_runs;
  w record;
begin
  if not cs_private.cs_can(p_workspace_id, 'read') then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  if not cs_private.cs_can(p_workspace_id, 'produce') then
    perform cs_private.cs_b3_refuse('capability_denied', null, 'CS403');
  end if;
  select * into v_run from public.cs_runs r where r.workspace_id = p_workspace_id and r.id = p_run_id for update;
  if v_run.id is null then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  if v_run.state in ('completed', 'failed', 'cancelled') or v_run.cancel_requested_at is not null then
    return v_run.state;
  end if;
  update public.cs_runs set cancel_requested_at = clock_timestamp() where workspace_id = p_workspace_id and id = p_run_id;
  perform cs_private.cs_append_run_event(p_workspace_id, p_run_id, 'run.cancel_requested',
    jsonb_build_object('actor_id', v_actor, 'reason', coalesce(nullif(btrim(coalesce(p_reason, '')), ''), 'requested')));
  -- not-yet-claimed provider work stops here; a leased item is stopped at its
  -- dispatch checkpoint (cs_mark_dispatching) and in-flight cost is still reconciled.
  for w in select wi.id, wi.node_id from public.cs_work_items wi
            where wi.workspace_id = p_workspace_id and wi.run_id = p_run_id
              and wi.state in ('queued', 'retry_wait') and wi.stage = 'provider_submit' for update loop
    update public.cs_work_items set state = 'cancelled', failure_code = 'run_cancelled' where id = w.id;
    perform cs_private.cs_set_node_state(p_workspace_id, p_run_id, w.node_id, 'cancelled',
      jsonb_build_object('code', 'run_cancelled'));
    perform cs_private.cs_release_unspent(r.id, 'not_dispatched')
       from public.cs_budget_reservations r
      where r.workspace_id = p_workspace_id and r.run_id = p_run_id and r.node_id = w.node_id
        and (r.attempt_id is null or exists (select 1 from public.cs_attempts a where a.id = r.attempt_id
                                               and a.state in ('prepared', 'cancelled')));
  end loop;
  insert into public.cs_audit_events (account_id, workspace_id, actor_id, action, affected_refs)
  select ws.account_id, p_workspace_id, v_actor, 'run.cancel',
         jsonb_build_array(jsonb_build_object('kind', 'run', 'id', p_run_id))
    from public.cs_workspaces ws where ws.id = p_workspace_id;
  return cs_private.cs_advance_run(p_workspace_id, p_run_id);
end $$;

-- -----------------------------------------------------------------------------
-- 13. Worker procedures (cs_worker). Every call is bound to a live lease.
-- -----------------------------------------------------------------------------
create or replace function cs_private.cs_b3_lease(p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint,
                                                  p_stage public.cs_work_stage)
returns public.cs_work_items language plpgsql set search_path = public, pg_temp as $$
declare v public.cs_work_items;
begin
  v := cs_private.cs_lock_live_lease(p_work_item_id, p_lease_token, p_fencing_token);
  if v.stage <> p_stage then
    perform cs_private.cs_b3_refuse('stage_mismatch', format('%s is not %s', v.stage, p_stage), 'CS409');
  end if;
  return v;
end $$;

create or replace function cs_private.cs_finish_item(p_item public.cs_work_items, p_state public.cs_work_state,
                                                     p_result_kind text, p_receipt_sha256 text, p_release_id text,
                                                     p_failure_code text default null)
returns void language plpgsql set search_path = public, pg_temp as $$
begin
  insert into public.cs_stage_results (workspace_id, work_item_id, fencing_token, result_kind, output_refs,
    receipt_sha256, release_id)
  values (p_item.workspace_id, p_item.id, p_item.fencing_token, p_result_kind, '[]'::jsonb, p_receipt_sha256, p_release_id);
  update public.cs_work_items
     set state = p_state, lease_token = null, lease_until = null, lease_owner = null, failure_code = p_failure_code
   where id = p_item.id;
end $$;

-- Resolve the node's inputs to exact AssetRefs (dependency outputs by
-- node/port/ordinal) and derive node_input_sha256 from compile hash, node id,
-- resolved refs and the frozen provider call.
create or replace function cs_private.cs_resolve_inputs(p_workspace_id uuid, p_run_id uuid, p_node_id text)
returns jsonb language plpgsql stable set search_path = public, pg_temp as $$
declare
  s public.cs_run_node_specs;
  i jsonb;
  v_ref jsonb;
  v_out jsonb := '[]'::jsonb;
begin
  select * into s from public.cs_run_node_specs x
   where x.workspace_id = p_workspace_id and x.run_id = p_run_id and x.node_id = p_node_id;
  for i in select * from jsonb_array_elements(s.inputs) loop
    if i #>> '{source,kind}' = 'asset' then
      v_ref := i #> '{source,asset}';
    else
      select rn.output_refs -> ((i #>> '{source,ordinal}')::integer) into v_ref
        from public.cs_run_nodes rn
       where rn.workspace_id = p_workspace_id and rn.run_id = p_run_id and rn.node_id = i #>> '{source,node_id}'
         and rn.state = 'succeeded';
      if v_ref is null then
        perform cs_private.cs_b3_refuse('dependency_output_missing',
          format('%s needs %s output %s', p_node_id, i #>> '{source,node_id}', i #>> '{source,ordinal}'), 'CS409');
      end if;
    end if;
    v_out := v_out || jsonb_build_array(jsonb_build_object('port', i ->> 'port', 'asset', v_ref));
  end loop;
  perform cs_private.cs_assert_asset_refs(p_workspace_id,
    coalesce((select jsonb_agg(x -> 'asset') from jsonb_array_elements(v_out) x), '[]'::jsonb));
  return v_out;
end $$;

create or replace function public.cs_prepare_attempt(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_adapter_revision text)
returns table (attempt_id uuid, attempt_number integer, attempt_state public.cs_attempt_state,
               provider_idempotency_key uuid, reservation_id uuid, catalogue_version_id uuid,
               provider_call jsonb, inputs jsonb, node_input_sha256 text, catalogue_entry jsonb, catalogue_surface text, adapter_config jsonb,
               credential_version_id uuid, provider_account_fingerprint text, compile_hash text, resumed boolean)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v public.cs_work_items;
  s public.cs_run_node_specs;
  a public.cs_attempts;
  adm public.cs_run_admissions;
  cred cs_private.cs_credentials;
  r public.cs_budget_reservations;
  c public.cs_provider_catalogue_versions;
  v_resumed boolean := true;
  v_inputs jsonb;
  v_hash text;
begin
  v := cs_private.cs_b3_lease(p_work_item_id, p_lease_token, p_fencing_token, 'provider_submit');
  select * into s from public.cs_run_node_specs x
   where x.workspace_id = v.workspace_id and x.run_id = v.run_id and x.node_id = v.node_id;
  select * into adm from public.cs_run_admissions x where x.workspace_id = v.workspace_id and x.run_id = v.run_id;
  select * into c from public.cs_provider_catalogue_versions x where x.id = s.catalogue_version_id;
  select * into a from public.cs_attempts x
   where x.workspace_id = v.workspace_id and x.run_id = v.run_id and x.node_id = v.node_id
   order by x.attempt_number desc limit 1 for update;
  v_inputs := cs_private.cs_resolve_inputs(v.workspace_id, v.run_id, v.node_id);
  v_hash := encode(sha256(convert_to(jsonb_build_object('compile_hash', adm.compile_hash, 'node_id', v.node_id,
              'inputs', v_inputs, 'provider_call', s.provider_call)::text, 'UTF8')), 'hex');
  if a.id is null then
    select * into cred from cs_private.cs_credentials x where x.version_id = adm.credential_version_id;
    select * into r from public.cs_budget_reservations x
     where x.workspace_id = v.workspace_id and x.run_id = v.run_id and x.node_id = v.node_id for update;
    if r.state <> 'reserved' then
      perform cs_private.cs_b3_refuse('reservation_unavailable', r.state::text, 'CS409');
    end if;
    insert into public.cs_attempts (workspace_id, run_id, node_id, attempt_number, provider, provider_account_fingerprint,
      credential_version_id, catalogue_version_id, adapter_revision, node_input_sha256, budget_reservation_id)
    values (v.workspace_id, v.run_id, v.node_id, 1, adm.provider, cred.provider_account_fingerprint,
            cred.version_id, s.catalogue_version_id, p_adapter_revision, v_hash, r.id)
    returning * into a;
    update public.cs_budget_reservations set attempt_id = a.id where id = r.id;
    insert into public.cs_attempt_events (workspace_id, attempt_id, sequence, state, reason_code)
    values (v.workspace_id, a.id, 1, 'prepared', 'admitted');
    perform cs_private.cs_append_run_event(v.workspace_id, v.run_id, 'attempt.prepared',
      jsonb_build_object('node_id', v.node_id, 'attempt_id', a.id, 'attempt_number', 1,
                         'node_input_sha256', v_hash, 'reservation_id', r.id));
    v_resumed := false;
  end if;
  return query select a.id, a.attempt_number, a.state, a.provider_idempotency_key, a.budget_reservation_id,
                      s.catalogue_version_id, s.provider_call, v_inputs, a.node_input_sha256, c.entry, c.surface, c.adapter_config,
                      a.credential_version_id, a.provider_account_fingerprint, adm.compile_hash, v_resumed;
end $$;

create or replace function public.cs_mark_dispatching(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_attempt_id uuid, p_envelope jsonb,
  p_envelope_sha256 text, p_dispatch_sha256 text, p_request_sha256 text)
returns timestamptz language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
  v_run public.cs_runs;
  a public.cs_attempts;
  v_now timestamptz := clock_timestamp();
begin
  v := cs_private.cs_b3_lease(p_work_item_id, p_lease_token, p_fencing_token, 'provider_submit');
  select * into v_run from public.cs_runs r where r.workspace_id = v.workspace_id and r.id = v.run_id for update;
  select * into a from public.cs_attempts x
   where x.workspace_id = v.workspace_id and x.id = p_attempt_id and x.run_id = v.run_id and x.node_id = v.node_id
   for update;
  if a.id is null then
    perform cs_private.cs_b3_refuse('resource_unavailable', 'attempt', 'CS404');
  end if;
  if a.state <> 'prepared' then
    perform cs_private.cs_b3_refuse('attempt_already_dispatched', a.state::text, 'CS409');
  end if;
  if v_run.cancel_requested_at is not null then
    perform cs_private.cs_b3_refuse('run_cancel_requested', null, 'CS409');
  end if;
  -- current eligibility is rechecked before any new paid dispatch
  if not cs_private.cs_role_allows(v_run.requested_by, v.workspace_id, 'produce') then
    perform cs_private.cs_b3_refuse('authorization_revoked', 'requester no longer produces in this workspace', 'CS403');
  end if;
  if not exists (select 1 from cs_private.cs_credentials c where c.version_id = a.credential_version_id and c.status = 'active') then
    perform cs_private.cs_b3_refuse('credential_revoked', null, 'CS403');
  end if;
  if not exists (select 1 from public.cs_budget_reservations r where r.id = a.budget_reservation_id and r.state = 'reserved') then
    perform cs_private.cs_b3_refuse('reservation_unavailable', null, 'CS409');
  end if;
  if (p_envelope ->> 'attempt_id') is distinct from a.id::text
     or (p_envelope ->> 'idempotency_key') is distinct from a.provider_idempotency_key::text
     or (p_envelope ->> 'workspace_id') is distinct from a.workspace_id::text then
    perform cs_private.cs_b3_refuse('dispatch_envelope_conflict', 'envelope does not bind this attempt', 'CS409');
  end if;
  insert into cs_private.cs_attempt_payloads (workspace_id, attempt_id, envelope, envelope_sha256, dispatch_sha256)
  values (a.workspace_id, a.id, p_envelope, p_envelope_sha256, p_dispatch_sha256);
  update public.cs_attempts set state = 'dispatching', request_sha256 = p_request_sha256,
         dispatch_envelope_sha256 = p_dispatch_sha256, dispatched_at = v_now where id = a.id;
  perform cs_private.cs_attempt_transition(a.workspace_id, a.id, 'dispatching', 'dispatch_marker_written');
  perform cs_private.cs_append_run_event(v.workspace_id, v.run_id, 'attempt.dispatched',
    jsonb_build_object('node_id', v.node_id, 'attempt_id', a.id, 'request_sha256', p_request_sha256,
                       'dispatch_envelope_sha256', p_dispatch_sha256, 'replay_count', a.replay_count));
  return v_now;
end $$;

-- A redelivered/duplicate provider_submit whose attempt already passed its
-- dispatch marker: close the item, never call the provider again.
create or replace function public.cs_close_duplicate_submit(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_release_id text)
returns text language plpgsql security definer set search_path = public, pg_temp as $$
declare v public.cs_work_items; a public.cs_attempts;
begin
  v := cs_private.cs_b3_lease(p_work_item_id, p_lease_token, p_fencing_token, 'provider_submit');
  select * into a from public.cs_attempts x
   where x.workspace_id = v.workspace_id and x.run_id = v.run_id and x.node_id = v.node_id
   order by x.attempt_number desc limit 1;
  if a.id is null or a.state in ('prepared', 'dispatching') then
    perform cs_private.cs_b3_refuse('not_a_duplicate', coalesce(a.state::text, 'no attempt'), 'CS409');
  end if;
  perform cs_private.cs_finish_item(v, 'done', 'duplicate_dispatch_refused',
    encode(sha256(convert_to(a.id::text, 'UTF8')), 'hex'), p_release_id, 'duplicate_dispatch_refused');
  perform cs_private.cs_append_run_event(v.workspace_id, v.run_id, 'work.duplicate_refused',
    jsonb_build_object('node_id', v.node_id, 'attempt_id', a.id, 'attempt_state', a.state));
  return a.state::text;
end $$;

-- An attempt that provably never reached the provider: release, and stop.
-- p_outcome: cancelled | refused | authorization_blocked
create or replace function public.cs_abandon_attempt(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_attempt_id uuid, p_outcome text,
  p_error jsonb, p_release_id text)
returns public.cs_run_state language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
  a public.cs_attempts;
  v_node public.cs_node_state;
begin
  v := cs_private.cs_b3_lease(p_work_item_id, p_lease_token, p_fencing_token, 'provider_submit');
  if p_outcome not in ('cancelled', 'refused', 'authorization_blocked') then
    perform cs_private.cs_b3_refuse('invalid_outcome', p_outcome);
  end if;
  select * into a from public.cs_attempts x
   where x.workspace_id = v.workspace_id and x.run_id = v.run_id and x.node_id = v.node_id
     and (p_attempt_id is null or x.id = p_attempt_id)
   order by x.attempt_number desc limit 1 for update;
  if a.id is not null then
    -- 'dispatching' is accepted only for a typed adapter refusal raised before
    -- the provider call (the caller passes that refusal code as evidence).
    if a.state not in ('prepared', 'dispatching') or (a.state = 'dispatching' and p_outcome <> 'refused') then
      perform cs_private.cs_b3_refuse('attempt_not_abandonable', a.state::text, 'CS409');
    end if;
    update public.cs_attempts set error = p_error where id = a.id;
    perform cs_private.cs_attempt_transition(a.workspace_id, a.id,
      case when p_outcome = 'cancelled' then 'cancelled'::public.cs_attempt_state else 'failed' end,
      coalesce(p_error ->> 'code', p_outcome));
  end if;
  perform cs_private.cs_release_unspent(r.id, coalesce('not_dispatched:' || (p_error ->> 'code'), 'not_dispatched'))
     from public.cs_budget_reservations r
    where r.workspace_id = v.workspace_id and r.run_id = v.run_id and r.node_id = v.node_id;
  v_node := case p_outcome when 'cancelled' then 'cancelled'::public.cs_node_state
                           when 'authorization_blocked' then 'authorization_blocked' else 'failed' end;
  perform cs_private.cs_set_node_state(v.workspace_id, v.run_id, v.node_id, v_node, p_error);
  perform cs_private.cs_finish_item(v, 'done', 'attempt_' || p_outcome,
    encode(sha256(convert_to(coalesce(p_error::text, p_outcome), 'UTF8')), 'hex'), p_release_id, p_outcome);
  return cs_private.cs_advance_run(v.workspace_id, v.run_id);
end $$;

-- Provider answered. p_jobs: [{provider_request_id, status, result_url?, raw_sha256}]
-- p_charges: [{billing_reference, certainty, credits|null, provider_request_id|null, raw_sha256, source}]
create or replace function public.cs_record_provider_outcome(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_attempt_id uuid, p_outcome text,
  p_jobs jsonb, p_charges jsonb, p_error jsonb, p_release_id text)
returns public.cs_run_state language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
  a public.cs_attempts;
  c public.cs_provider_catalogue_versions;
  j jsonb;
  ch jsonb;
  v_ord integer := 0;
  v_receipt uuid;
  v_total bigint := 0;
  v_units numeric := 0;
  v_known boolean := true;
  v_amount bigint;
  v_ids text[] := '{}';
  v_snapshot jsonb;
begin
  v := cs_private.cs_b3_lease(p_work_item_id, p_lease_token, p_fencing_token, 'provider_submit');
  if p_outcome not in ('completed', 'failed') then
    perform cs_private.cs_b3_refuse('invalid_outcome', p_outcome);
  end if;
  select * into a from public.cs_attempts x
   where x.workspace_id = v.workspace_id and x.id = p_attempt_id and x.run_id = v.run_id and x.node_id = v.node_id
   for update;
  if a.id is null or a.state <> 'dispatching' then
    perform cs_private.cs_b3_refuse('attempt_not_dispatching', coalesce(a.state::text, 'missing'), 'CS409');
  end if;
  select * into c from public.cs_provider_catalogue_versions x where x.id = a.catalogue_version_id;
  v_snapshot := jsonb_build_object('credit_price_micros', c.credit_price_micros, 'catalogue_version_id', c.id,
                                   'source_url', c.price_snapshot ->> 'source_url',
                                   'observed_at', c.price_snapshot ->> 'observed_at', 'rounding', 'ceil');
  if jsonb_typeof(p_jobs) <> 'array' or jsonb_typeof(p_charges) <> 'array'
     or (p_outcome = 'completed' and jsonb_array_length(p_jobs) = 0) then
    perform cs_private.cs_b3_refuse('provider_response_invalid', 'jobs/charges must be arrays');
  end if;

  for j in select * from jsonb_array_elements(p_jobs) loop
    insert into public.cs_provider_receipts (workspace_id, run_id, node_id, attempt_id, provider,
      provider_account_fingerprint, provider_request_id, kind, status, source, source_event_key,
      charge_amount_micros, charge_evidence, charge_certainty, billing_reference, dispatch_envelope_sha256,
      raw_receipt_sha256, note)
    values (a.workspace_id, a.run_id, a.node_id, a.id, a.provider, a.provider_account_fingerprint,
            j ->> 'provider_request_id', 'output', j ->> 'status', 'authenticated_response',
            'output:' || (j ->> 'provider_request_id'), null, 'unknown', 'not_applicable', j ->> 'billing_reference',
            a.dispatch_envelope_sha256, j ->> 'raw_sha256', 'charge is carried by the billing receipt')
    returning id into v_receipt;
    v_ids := v_ids || (j ->> 'provider_request_id');
    if p_outcome = 'completed' and j ->> 'status' = 'completed' then
      insert into cs_private.cs_attempt_outputs (workspace_id, attempt_id, ordinal, provider_job_id, result_url)
      values (a.workspace_id, a.id, v_ord, j ->> 'provider_request_id', j ->> 'result_url');
      v_ord := v_ord + 1;
    end if;
  end loop;

  for ch in select * from jsonb_array_elements(p_charges) loop
    if ch ->> 'certainty' in ('provider_reported', 'balance_delta') and (ch ->> 'credits') is not null then
      v_amount := ceil((ch ->> 'credits')::numeric * c.credit_price_micros)::bigint;
      v_total := v_total + v_amount;
      v_units := v_units + (ch ->> 'credits')::numeric;
    else
      v_amount := null;
      v_known := false;
    end if;
    insert into public.cs_provider_receipts (workspace_id, run_id, node_id, attempt_id, provider,
      provider_account_fingerprint, provider_request_id, kind, status, source, source_event_key,
      charge_amount_micros, charge_evidence, charge_certainty, billing_reference, provider_units, provider_unit_name,
      conversion_snapshot, dispatch_envelope_sha256, raw_receipt_sha256, note)
    values (a.workspace_id, a.run_id, a.node_id, a.id, a.provider, a.provider_account_fingerprint,
            ch ->> 'provider_request_id', 'billing', case when p_outcome = 'completed' then 'completed' else 'failed' end,
            'authenticated_response', 'billing:' || (ch ->> 'billing_reference'), v_amount,
            case when v_amount is null then 'unknown' else 'provider_billing' end, ch ->> 'certainty',
            ch ->> 'billing_reference', (ch ->> 'credits')::numeric, 'credits',
            case when v_amount is null then null else v_snapshot end,
            a.dispatch_envelope_sha256, ch ->> 'raw_sha256',
            left(coalesce(ch ->> 'note', format('credits=%s certainty=%s', coalesce(ch ->> 'credits', 'null'), ch ->> 'certainty')), 2000))
    returning id into v_receipt;
  end loop;
  if jsonb_array_length(p_charges) = 0 then
    v_known := false;
  end if;

  update public.cs_attempts set provider_request_ids = v_ids, error = p_error where id = a.id;
  if cardinality(v_ids) > 0 then
    perform cs_private.cs_append_run_event(a.workspace_id, a.run_id, 'attempt.provider_bound',
      jsonb_build_object('node_id', a.node_id, 'attempt_id', a.id, 'provider', a.provider,
                         'provider_request_id', v_ids[1]));
  end if;
  if v_known then
    perform cs_private.cs_settle(a.budget_reservation_id, v_total, v_receipt, v_units, v_snapshot, '');
  else
    perform cs_private.cs_mark_unknown(a.budget_reservation_id, 'charge_not_evidenced');
  end if;
  perform cs_private.cs_append_run_event(a.workspace_id, a.run_id, 'cost.reconciled',
    jsonb_build_object('node_id', a.node_id, 'attempt_id', a.id, 'receipt_id', v_receipt,
                       'cost', cs_private.cs_run_cost(a.workspace_id, a.run_id) - 'receipt_ids'));

  if p_outcome = 'completed' then
    perform cs_private.cs_attempt_transition(a.workspace_id, a.id, 'ingesting', 'provider_completed', v_receipt);
    insert into public.cs_work_items (workspace_id, environment_id, run_id, node_id, stage, dedupe_key, max_attempts,
      payload_ref)
    values (v.workspace_id, v.environment_id, v.run_id, v.node_id, 'ingest_original', 'ingest:' || a.id, 3,
            jsonb_build_object('attempt_id', a.id))
    on conflict (environment_id, dedupe_key) do nothing;
    perform cs_private.cs_finish_item(v, 'done', 'provider_submitted',
      encode(sha256(convert_to(array_to_string(v_ids, ','), 'UTF8')), 'hex'), p_release_id);
  else
    perform cs_private.cs_attempt_transition(a.workspace_id, a.id, 'failed', coalesce(p_error ->> 'code', 'provider_failed'), v_receipt);
    perform cs_private.cs_set_node_state(v.workspace_id, v.run_id, v.node_id, 'failed', p_error);
    perform cs_private.cs_finish_item(v, 'done', 'provider_failed',
      encode(sha256(convert_to(coalesce(p_error::text, 'failed'), 'UTF8')), 'hex'), p_release_id, 'provider_failed');
  end if;
  return cs_private.cs_advance_run(v.workspace_id, v.run_id);
end $$;

-- The provider call's outcome is unknown (crash/timeout/ambiguous transport
-- after the dispatch marker). May have been charged: keep the whole hold.
create or replace function cs_private.cs_mark_attempt_uncertain(p_item public.cs_work_items, p_attempt public.cs_attempts,
                                                                p_error jsonb, p_release_id text)
returns public.cs_run_state language plpgsql set search_path = public, pg_temp as $$
begin
  update public.cs_attempts set error = p_error where id = p_attempt.id;
  perform cs_private.cs_attempt_transition(p_attempt.workspace_id, p_attempt.id, 'uncertain',
                                           coalesce(p_error ->> 'code', 'dispatch_uncertain'));
  perform cs_private.cs_mark_unknown(p_attempt.budget_reservation_id, coalesce(p_error ->> 'code', 'dispatch_uncertain'));
  perform cs_private.cs_append_run_event(p_attempt.workspace_id, p_attempt.run_id, 'attempt.uncertain',
    jsonb_build_object('node_id', p_attempt.node_id, 'attempt_id', p_attempt.id, 'error', p_error,
                       'may_have_been_charged', true,
                       -- the CLI transport carries no provider idempotency key: no same-intent replay
                       'same_intent_replay_allowed', false));
  perform cs_private.cs_set_node_state(p_attempt.workspace_id, p_attempt.run_id, p_attempt.node_id,
                                       'needs_reconciliation', p_error);
  insert into public.cs_stage_results (workspace_id, work_item_id, fencing_token, result_kind, output_refs,
    receipt_sha256, release_id)
  values (p_item.workspace_id, p_item.id, greatest(p_item.fencing_token, 1), 'dispatch_uncertain', '[]'::jsonb,
          encode(sha256(convert_to(p_error::text, 'UTF8')), 'hex'), p_release_id)
  on conflict (work_item_id, fencing_token, result_kind) do nothing;
  update public.cs_work_items set state = 'dead', lease_token = null, lease_until = null, lease_owner = null,
         failure_code = 'dispatch_uncertain' where id = p_item.id;
  return cs_private.cs_advance_run(p_attempt.workspace_id, p_attempt.run_id);
end $$;

create or replace function public.cs_record_dispatch_uncertain(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_attempt_id uuid, p_error jsonb, p_release_id text)
returns public.cs_run_state language plpgsql security definer set search_path = public, pg_temp as $$
declare v public.cs_work_items; a public.cs_attempts;
begin
  v := cs_private.cs_b3_lease(p_work_item_id, p_lease_token, p_fencing_token, 'provider_submit');
  select * into a from public.cs_attempts x
   where x.workspace_id = v.workspace_id and x.id = p_attempt_id and x.run_id = v.run_id and x.node_id = v.node_id
   for update;
  if a.id is null or a.state <> 'dispatching' then
    perform cs_private.cs_b3_refuse('attempt_not_dispatching', coalesce(a.state::text, 'missing'), 'CS409');
  end if;
  return cs_private.cs_mark_attempt_uncertain(v, a, p_error, p_release_id);
end $$;

create or replace function public.cs_ingest_targets(p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint)
returns table (attempt_id uuid, ordinal integer, provider_job_id text, result_url text, output_port text,
               result_origins jsonb)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare v public.cs_work_items;
begin
  v := cs_private.cs_b3_lease(p_work_item_id, p_lease_token, p_fencing_token, 'ingest_original');
  return query
    select o.attempt_id, o.ordinal, o.provider_job_id, o.result_url,
           coalesce(s.provider_call #>> '{output_port}', 'image'), coalesce(c.adapter_config -> 'result_origins', '[]')
      from cs_private.cs_attempt_outputs o
      join public.cs_attempts a on a.workspace_id = o.workspace_id and a.id = o.attempt_id
      join public.cs_provider_catalogue_versions c on c.id = a.catalogue_version_id
      join public.cs_run_node_specs s on s.workspace_id = a.workspace_id and s.run_id = a.run_id and s.node_id = a.node_id
     where o.workspace_id = v.workspace_id and o.attempt_id = (v.payload_ref ->> 'attempt_id')::uuid
       and a.run_id = v.run_id and a.node_id = v.node_id
     order by o.ordinal;
end $$;

create or replace function public.cs_complete_ingest(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_output_refs jsonb, p_release_id text)
returns public.cs_run_state language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
  a public.cs_attempts;
  r jsonb;
  v_expected integer;
  v_seq bigint;
begin
  v := cs_private.cs_b3_lease(p_work_item_id, p_lease_token, p_fencing_token, 'ingest_original');
  select * into a from public.cs_attempts x
   where x.workspace_id = v.workspace_id and x.id = (v.payload_ref ->> 'attempt_id')::uuid for update;
  if a.id is null or a.state <> 'ingesting' then
    perform cs_private.cs_b3_refuse('attempt_not_ingesting', coalesce(a.state::text, 'missing'), 'CS409');
  end if;
  perform cs_private.cs_assert_asset_refs(v.workspace_id, p_output_refs);
  select count(*) into v_expected from cs_private.cs_attempt_outputs o
   where o.workspace_id = a.workspace_id and o.attempt_id = a.id;
  if jsonb_array_length(p_output_refs) <> v_expected then
    perform cs_private.cs_b3_refuse('outputs_incomplete', format('%s of %s outputs', jsonb_array_length(p_output_refs), v_expected));
  end if;
  -- every ref must be a version committed under THIS lease (provenance)
  if exists (select 1 from jsonb_array_elements(p_output_refs) x
              where not exists (select 1 from public.cs_asset_versions av
                                 where av.workspace_id = v.workspace_id and av.id::text = x ->> 'asset_version_id'
                                   and av.work_item_id = v.id and av.fencing_token = v.fencing_token)) then
    perform cs_private.cs_b3_refuse('output_provenance_mismatch', null, 'CS409');
  end if;
  insert into public.cs_stage_results (workspace_id, work_item_id, fencing_token, result_kind, output_refs,
    receipt_sha256, release_id)
  values (v.workspace_id, v.id, v.fencing_token, 'ingested', p_output_refs,
          encode(sha256(convert_to(p_output_refs::text, 'UTF8')), 'hex'), p_release_id);
  update public.cs_work_items set state = 'done', lease_token = null, lease_until = null, lease_owner = null,
         failure_code = null where id = v.id;
  for r in select * from jsonb_array_elements(p_output_refs) loop
    perform cs_private.cs_append_run_event(v.workspace_id, v.run_id, 'asset.persisted',
      jsonb_build_object('node_id', v.node_id, 'attempt_id', a.id, 'asset', r, 'output_port', 'image',
                         'size_bytes', (select av.size_bytes from public.cs_asset_versions av
                                         where av.workspace_id = v.workspace_id and av.id::text = r ->> 'asset_version_id')));
  end loop;
  perform cs_private.cs_attempt_transition(a.workspace_id, a.id, 'succeeded', 'outputs_persisted');
  perform cs_private.cs_set_node_state(v.workspace_id, v.run_id, v.node_id, 'succeeded');
  update public.cs_run_nodes set output_refs = p_output_refs
   where workspace_id = v.workspace_id and run_id = v.run_id and node_id = v.node_id;
  return cs_private.cs_advance_run(v.workspace_id, v.run_id);
end $$;

-- Lease recovery after worker death. Non-paid stages are re-claimed by
-- cs_claim_work itself; a paid provider_submit whose lease expired is resolved
-- from durable attempt state: no dispatch marker -> safe to re-queue;
-- dispatch marker without a recorded outcome -> uncertain, never resubmitted.
create or replace function public.cs_recover_expired_leases(p_environment_id text, p_limit integer, p_release_id text)
returns table (work_item_id uuid, run_id uuid, node_id text, action text)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  w public.cs_work_items;
  a public.cs_attempts;
begin
  if p_limit is null or p_limit < 1 or p_limit > 16 then
    perform cs_private.cs_b3_refuse('claim_limit_out_of_bounds', null);
  end if;
  for w in select * from public.cs_work_items x
            where x.environment_id = p_environment_id and x.state = 'leased' and x.lease_until <= clock_timestamp()
              and x.stage = 'provider_submit'
            order by x.lease_until, x.id limit p_limit for update skip locked loop
    select * into a from public.cs_attempts x
     where x.workspace_id = w.workspace_id and x.run_id = w.run_id and x.node_id = w.node_id
     order by x.attempt_number desc limit 1 for update;
    if a.id is null or a.state = 'prepared' then
      update public.cs_work_items set state = case when attempt_count >= max_attempts then 'dead'::public.cs_work_state
                                                   else 'retry_wait' end,
             lease_token = null, lease_until = null, lease_owner = null, due_at = clock_timestamp(),
             failure_code = 'lease_expired_before_dispatch'
       where id = w.id;
      perform cs_private.cs_append_run_event(w.workspace_id, w.run_id, 'work.lease_recovered',
        jsonb_build_object('node_id', w.node_id, 'stage', w.stage, 'action', 'requeue_before_dispatch'));
      perform cs_private.cs_set_node_state(w.workspace_id, w.run_id, w.node_id, 'queued');
      work_item_id := w.id; run_id := w.run_id; node_id := w.node_id; action := 'requeued_before_dispatch';
    elsif a.state = 'dispatching' then
      perform cs_private.cs_mark_attempt_uncertain(w, a,
        jsonb_build_object('code', 'worker_lost_during_dispatch', 'message',
                           'The worker stopped after the dispatch marker; the provider may have charged.',
                           'retryable', false, 'field_paths', '[]'::jsonb, 'related_ids', jsonb_build_array(a.id)),
        p_release_id);
      work_item_id := w.id; run_id := w.run_id; node_id := w.node_id; action := 'needs_reconciliation';
    else
      update public.cs_work_items set state = 'done', lease_token = null, lease_until = null, lease_owner = null
       where id = w.id;
      work_item_id := w.id; run_id := w.run_id; node_id := w.node_id; action := 'already_recorded';
    end if;
    return next;
  end loop;
  -- ingest that exhausted its bounded retries: the provider charge stands
  -- (already recorded), the outputs could not be persisted -> node failed.
  for w in select x.* from public.cs_work_items x
             join public.cs_runs r on r.workspace_id = x.workspace_id and r.id = x.run_id
            where x.environment_id = p_environment_id and x.state = 'dead'
              and x.stage in ('ingest_original', 'provider_submit')
              and coalesce(x.failure_code, '') <> 'dispatch_uncertain'
              and r.state not in ('completed', 'failed', 'cancelled')
              and (exists (select 1 from public.cs_attempts at where at.workspace_id = x.workspace_id
                            and at.run_id = x.run_id and at.node_id = x.node_id and at.state in ('prepared', 'ingesting'))
                   or (not exists (select 1 from public.cs_attempts at where at.workspace_id = x.workspace_id
                                    and at.run_id = x.run_id and at.node_id = x.node_id)
                       and exists (select 1 from public.cs_budget_reservations br where br.workspace_id = x.workspace_id
                                    and br.run_id = x.run_id and br.node_id = x.node_id and br.state = 'reserved'))
                   or (r.state in ('queued', 'running', 'cancel_requested')
                       and exists (select 1 from public.cs_run_nodes rn where rn.workspace_id = x.workspace_id
                                    and rn.run_id = x.run_id and rn.node_id = x.node_id and rn.state = 'failed')))
            order by x.updated_at, x.id limit p_limit for update of x skip locked loop
    select * into a from public.cs_attempts x
     where x.workspace_id = w.workspace_id and x.run_id = w.run_id and x.node_id = w.node_id
     order by x.attempt_number desc limit 1 for update;
    if a.state = 'ingesting' then
      perform cs_private.cs_attempt_transition(a.workspace_id, a.id, 'failed', 'output_ingest_failed');
    elsif a.state = 'prepared' then
      -- bounded pre-dispatch retries exhausted: never dispatched, nothing charged
      perform cs_private.cs_attempt_transition(a.workspace_id, a.id, 'failed', coalesce(w.failure_code, 'retries_exhausted'));
      perform cs_private.cs_release_unspent(a.budget_reservation_id, 'not_dispatched:retries_exhausted');
    elsif a.id is null then
      perform cs_private.cs_release_unspent(r.id, 'not_dispatched:retries_exhausted')
         from public.cs_budget_reservations r
        where r.workspace_id = w.workspace_id and r.run_id = w.run_id and r.node_id = w.node_id;
    end if;
    perform cs_private.cs_set_node_state(w.workspace_id, w.run_id, w.node_id, 'failed',
      jsonb_build_object('code', case when w.stage = 'ingest_original' then 'output_ingest_failed'
                                      else 'retries_exhausted' end, 'detail', w.failure_code));
    perform cs_private.cs_advance_run(w.workspace_id, w.run_id);
    work_item_id := w.id; run_id := w.run_id; node_id := w.node_id;
    action := case when w.stage = 'ingest_original' then 'ingest_failed' else 'submit_retries_exhausted' end;
    return next;
  end loop;
end $$;

-- -----------------------------------------------------------------------------
-- 14. Operator reconciliation (no runtime grant; run by an operator session).
--     The named actor must be a current admin of the workspace's account.
-- -----------------------------------------------------------------------------
create or replace function public.cs_reconcile_attempt(
  p_attempt_id uuid, p_actor_id uuid, p_charged_micros bigint, p_billing_reference text, p_rationale text)
returns public.cs_run_state language plpgsql security definer set search_path = public, pg_temp as $$
declare
  a public.cs_attempts;
  v_account uuid;
  v_receipt uuid;
begin
  select * into a from public.cs_attempts where id = p_attempt_id for update;
  if a.id is null then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  select w.account_id into v_account from public.cs_workspaces w where w.id = a.workspace_id;
  if not exists (select 1 from public.cs_account_members m where m.account_id = v_account and m.user_id = p_actor_id
                  and m.revoked_at is null) then
    perform cs_private.cs_b3_refuse('capability_denied', 'actor is not an admin of this account', 'CS403');
  end if;
  if p_rationale is null or length(btrim(p_rationale)) = 0 or p_charged_micros is null or p_charged_micros < 0 then
    perform cs_private.cs_b3_refuse('reconciliation_invalid', 'charged amount and rationale are required');
  end if;
  if not exists (select 1 from public.cs_budget_reservations r where r.id = a.budget_reservation_id and r.state = 'unknown') then
    perform cs_private.cs_b3_refuse('reconciliation_not_needed', 'reservation is not in unknown state', 'CS409');
  end if;
  insert into public.cs_provider_receipts (workspace_id, run_id, node_id, attempt_id, provider,
    provider_account_fingerprint, kind, status, source, source_event_key, charge_amount_micros, charge_evidence,
    charge_certainty, billing_reference, dispatch_envelope_sha256, raw_receipt_sha256, actor_id, note)
  values (a.workspace_id, a.run_id, a.node_id, a.id, a.provider, a.provider_account_fingerprint, 'reconciliation',
          'unknown', 'operator', 'reconciliation:' || a.id, p_charged_micros, 'operator_reconciliation',
          'operator_reconciliation', p_billing_reference,
          coalesce(a.dispatch_envelope_sha256, encode(sha256(convert_to(a.id::text, 'UTF8')), 'hex')),
          encode(sha256(convert_to(coalesce(p_billing_reference, '') || p_rationale, 'UTF8')), 'hex'),
          p_actor_id, btrim(p_rationale))
  returning id into v_receipt;
  perform cs_private.cs_settle(a.budget_reservation_id, p_charged_micros, v_receipt, null,
                               jsonb_build_object('evidence', 'operator_reconciliation', 'actor_id', p_actor_id), ':reconciliation');
  if a.state in ('uncertain', 'dispatching') then
    perform cs_private.cs_attempt_transition(a.workspace_id, a.id, 'failed', 'operator_reconciled', v_receipt);
  end if;
  perform cs_private.cs_set_node_state(a.workspace_id, a.run_id, a.node_id, 'failed',
    jsonb_build_object('code', 'reconciled_without_output'));
  insert into public.cs_audit_events (account_id, workspace_id, actor_id, action, affected_refs, receipt_refs)
  values (v_account, a.workspace_id, p_actor_id, 'attempt.reconcile',
          jsonb_build_array(jsonb_build_object('kind', 'attempt', 'id', a.id)),
          jsonb_build_array(jsonb_build_object('kind', 'provider_receipt', 'id', v_receipt)));
  return cs_private.cs_advance_run(a.workspace_id, a.run_id);
end $$;

-- Worker: decrypt input for the credential bound to THIS attempt only.
create or replace function public.cs_attempt_credential(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_attempt_id uuid)
returns table (credential_id uuid, version_id uuid, mode public.cs_credential_mode, provider_account_fingerprint text,
               ciphertext bytea, nonce bytea, encryption_key_version text)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare v public.cs_work_items;
begin
  v := cs_private.cs_b3_lease(p_work_item_id, p_lease_token, p_fencing_token, 'provider_submit');
  return query
    select c.id, c.version_id, c.mode, c.provider_account_fingerprint, c.ciphertext, c.nonce, c.encryption_key_version
      from public.cs_attempts a join cs_private.cs_credentials c on c.version_id = a.credential_version_id
     where a.workspace_id = v.workspace_id and a.id = p_attempt_id and a.run_id = v.run_id and a.node_id = v.node_id
       and c.status = 'active';
end $$;

-- Receipts of one run for the cost export (current read authority).
create or replace function public.cs_list_run_receipts(p_workspace_id uuid, p_run_id uuid)
returns table (id uuid, workspace_id uuid, run_id uuid, node_id text, attempt_id uuid, observed_at timestamptz,
               provider text, credential_version_id uuid, provider_request_id text, kind text, status text,
               dispatch_envelope_sha256 text, raw_receipt_sha256 text, charge_amount_micros bigint,
               charge_evidence text, billing_reference text, signature_verified boolean, source text, actor_id uuid,
               note text, charge_certainty text, provider_units numeric, attempt_state text, output_refs jsonb)
language plpgsql stable security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
begin
  perform cs_private.cs_require_actor();
  if not cs_private.cs_can(p_workspace_id, 'read')
     or not exists (select 1 from public.cs_runs r where r.workspace_id = p_workspace_id and r.id = p_run_id) then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  return query
    select pr.id, pr.workspace_id, pr.run_id, pr.node_id, pr.attempt_id, pr.observed_at, pr.provider,
           a.credential_version_id, pr.provider_request_id, pr.kind, pr.status, pr.dispatch_envelope_sha256,
           pr.raw_receipt_sha256, pr.charge_amount_micros, pr.charge_evidence, pr.billing_reference,
           pr.signature_verified, pr.source, pr.actor_id, pr.note, pr.charge_certainty, pr.provider_units,
           a.state::text, rn.output_refs
      from public.cs_provider_receipts pr
      join public.cs_attempts a on a.workspace_id = pr.workspace_id and a.id = pr.attempt_id
      join public.cs_run_nodes rn on rn.workspace_id = pr.workspace_id and rn.run_id = pr.run_id
                                 and rn.node_id = pr.node_id
     where pr.workspace_id = p_workspace_id and pr.run_id = p_run_id
     order by pr.observed_at, pr.id;
end $$;

-- Read-only cost projection for API callers (current read authority).
create or replace function public.cs_get_run_cost(p_workspace_id uuid, p_run_id uuid)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $$
begin
  perform cs_private.cs_require_actor();
  if not cs_private.cs_can(p_workspace_id, 'read')
     or not exists (select 1 from public.cs_runs r where r.workspace_id = p_workspace_id and r.id = p_run_id) then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  return cs_private.cs_run_cost(p_workspace_id, p_run_id);
end $$;

-- -----------------------------------------------------------------------------
-- 15. Function privileges
-- -----------------------------------------------------------------------------
do $$
declare r record;
begin
  for r in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where (n.nspname = 'public' and p.proname in ('cs_publish_catalogue_entry', 'cs_set_budget_policy',
                     'cs_store_workspace_credential', 'cs_revoke_workspace_credential', 'cs_quote_run', 'cs_admit_run',
                     'cs_approve_draft', 'cs_request_cancel', 'cs_prepare_attempt', 'cs_mark_dispatching',
                     'cs_abandon_attempt', 'cs_close_duplicate_submit', 'cs_record_provider_outcome', 'cs_record_dispatch_uncertain',
                     'cs_ingest_targets', 'cs_complete_ingest', 'cs_recover_expired_leases', 'cs_reconcile_attempt',
                     'cs_attempt_credential', 'cs_get_run_cost', 'cs_list_run_receipts'))
               or (n.nspname = 'cs_private' and p.proname in ('cs_b3_refuse', 'cs_set_node_state',
                     'cs_attempt_transition', 'cs_release_unspent', 'cs_mark_unknown', 'cs_settle',
                     'cs_budget_consumed', 'cs_current_policy', 'cs_run_cost', 'cs_advance_run', 'cs_quote_nodes',
                     'cs_b3_lease', 'cs_finish_item', 'cs_resolve_inputs', 'cs_mark_attempt_uncertain', 'cs_attempt_guard',
                     'cs_reservation_guard')) loop
    execute format('revoke all on function %s from public, anon, authenticated, cs_api, cs_worker', r.sig);
  end loop;
end $$;

grant execute on function
  public.cs_set_budget_policy(uuid, uuid, public.cs_credential_mode, integer, bigint, bigint, timestamptz, uuid, text),
  public.cs_store_workspace_credential(uuid, text, text, text, bytea, bytea, text),
  public.cs_revoke_workspace_credential(uuid, text),
  public.cs_quote_run(uuid, jsonb, public.cs_render_tier),
  public.cs_admit_run(uuid, text, text, uuid, text, text, bigint, jsonb, public.cs_render_tier, uuid, uuid, text),
  public.cs_approve_draft(uuid, uuid, text, uuid, text),
  public.cs_request_cancel(uuid, uuid, text),
  public.cs_get_run_cost(uuid, uuid),
  public.cs_list_run_receipts(uuid, uuid)
  to cs_api;
grant execute on function
  public.cs_prepare_attempt(uuid, uuid, bigint, text),
  public.cs_mark_dispatching(uuid, uuid, bigint, uuid, jsonb, text, text, text),
  public.cs_abandon_attempt(uuid, uuid, bigint, uuid, text, jsonb, text),
  public.cs_close_duplicate_submit(uuid, uuid, bigint, text),
  public.cs_record_provider_outcome(uuid, uuid, bigint, uuid, text, jsonb, jsonb, jsonb, text),
  public.cs_record_dispatch_uncertain(uuid, uuid, bigint, uuid, jsonb, text),
  public.cs_ingest_targets(uuid, uuid, bigint),
  public.cs_complete_ingest(uuid, uuid, bigint, jsonb, text),
  public.cs_recover_expired_leases(text, integer, text),
  public.cs_attempt_credential(uuid, uuid, bigint, uuid)
  to cs_worker;
-- cs_publish_catalogue_entry and cs_reconcile_attempt: deliberately no runtime grant (operator only).
-- Trigger functions are invoked by the table owner context; cs_worker needs
-- cs_reject_mutation already (granted by CS-B1).
