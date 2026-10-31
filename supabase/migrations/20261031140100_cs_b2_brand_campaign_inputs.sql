-- =============================================================================
-- CS-B2 / NS-10800 / PT-9636 — Brand CI + campaign input adapters
--
-- Implements the CS-B2 rows of docs/creative-studio/platform/database-contract.json
-- on top of CS-B1 (20261030210000):
--   * cs_external_bindings   verified Studio <-> MOS / Brand CI workspace mapping
--   * cs_operations (+events) durable pre-run owner; brand_package_import admission,
--                            and the contract's exactly-one-owner rule on cs_work_items
--   * cs_source_imports      immutable approved brand packages (brand-package-v1)
--   * cs_source_asset_mirrors exact source-asset -> Studio asset-version mirrors (§4)
--   * cs_source_heads        last observed upstream approval state (staleness, §5)
--   * cs_creative_request_versions / cs_treatment_versions  immutable inputs (§6)
--   * cs_authoring_drafts    non-executable optimistic drafts (§7)
--   * action-specific brand eligibility enforced in SQL (approval current, rights,
--     blocking unresolved decisions) with typed refusals.
--
-- Document digests are RFC 8785 (computed by the service); every write binds the
-- stored jsonb to the canonical text whose SHA-256 is the declared digest, so a
-- stored document can never disagree with its hash.
--
-- Not here: recipe promotion/compile (CS-B4/B3), paid treatment authoring and its
-- budget/attempts (CS-B3), credentials/service clients (CS-B5). Nothing is faked
-- for them. Re-runnable: IF NOT EXISTS / OR REPLACE; policies dropped by name.
-- =============================================================================

do $$
begin
  if to_regclass('public.cs_work_items') is null or to_regclass('public.cs_asset_versions') is null then
    raise exception 'cs_b2: CS-B1 foundation (20261030210000) must be applied first';
  end if;
  if not exists (select 1 from pg_roles where rolname = current_user and (rolsuper or rolbypassrls)) then
    raise exception 'cs_b2: migration owner % must be superuser or BYPASSRLS', current_user;
  end if;
end $$;

-- -----------------------------------------------------------------------------
-- 1. Enumerations (database-contract.json "enums")
-- -----------------------------------------------------------------------------
do $$
begin
  if to_regtype('public.cs_operation_kind') is null then
    create type public.cs_operation_kind as enum ('compile', 'brand_package_import', 'treatment_authoring');
  end if;
  if to_regtype('public.cs_operation_state') is null then
    create type public.cs_operation_state as enum ('queued', 'running', 'waiting_provider', 'needs_reconciliation',
      'budget_blocked', 'authorization_blocked', 'cancel_requested', 'completed', 'failed', 'cancelled');
  end if;
  if to_regtype('public.cs_external_authority') is null then
    create type public.cs_external_authority as enum ('marketing-os', 'brand-ci-studio');
  end if;
end $$;

-- -----------------------------------------------------------------------------
-- 2. Helpers
-- -----------------------------------------------------------------------------
-- Binds a stored document to its declared digest: the canonical text (produced
-- by the service, RFC 8785) must parse to exactly the document minus its own
-- top-level sha256, and SHA-256(canonical UTF-8) must equal document.sha256.
create or replace function cs_private.cs_assert_document_digest(p_document jsonb, p_canonical text)
returns text
language plpgsql immutable set search_path = public, pg_temp as $$
declare v_sha text;
begin
  if p_document is null or jsonb_typeof(p_document) <> 'object' or p_canonical is null then
    raise exception 'invalid_document' using errcode = 'CS422';
  end if;
  v_sha := p_document ->> 'sha256';
  if v_sha is null or v_sha !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid_document_digest' using errcode = 'CS422';
  end if;
  if p_canonical::jsonb is distinct from (p_document - 'sha256') then
    raise exception 'document_digest_mismatch: canonical text is not this document' using errcode = 'CS422';
  end if;
  if encode(sha256(convert_to(p_canonical, 'UTF8')), 'hex') <> v_sha then
    raise exception 'document_digest_mismatch: sha256 does not match canonical text' using errcode = 'CS422';
  end if;
  return v_sha;
exception when invalid_text_representation then
  raise exception 'invalid_document: canonical text is not JSON' using errcode = 'CS422';
end $$;

create or replace function cs_private.cs_is_version_ref(p jsonb) returns boolean
language sql immutable set search_path = public, pg_temp as $$
  select p is not null and jsonb_typeof(p) = 'object'
     and (p ->> 'authority') in ('creative-studio', 'brand-ci-studio', 'marketing-os')
     and coalesce(p ->> 'workspace_id', '') ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
     and coalesce(p ->> 'id', '') ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
     and coalesce(p ->> 'version_id', '') ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
     and jsonb_typeof(p -> 'revision') = 'number' and (p ->> 'revision')::numeric >= 1
     and coalesce(p ->> 'sha256', '') ~ '^[0-9a-f]{64}$'
     and length(coalesce(p ->> 'kind', '')) between 1 and 64;
$$;

-- Saved documents carry server-assigned audit fields: the verified actor and a
-- creation time close to the database clock (no back/forward dating).
create or replace function cs_private.cs_assert_audit(p_document jsonb, p_workspace_id uuid, p_actor uuid)
returns void
language plpgsql stable set search_path = public, pg_temp as $$
begin
  if (p_document ->> 'workspace_id') is distinct from p_workspace_id::text then
    raise exception 'scope_mismatch: document workspace' using errcode = 'CS403';
  end if;
  if (p_document ->> 'created_by') is distinct from p_actor::text then
    raise exception 'actor_mismatch: created_by must be the signed-in user' using errcode = 'CS422';
  end if;
  if abs(extract(epoch from ((p_document ->> 'created_at')::timestamptz - now()))) > 300 then
    raise exception 'timestamp_out_of_bounds: created_at' using errcode = 'CS422';
  end if;
exception when invalid_datetime_format or datetime_field_overflow then
  raise exception 'timestamp_out_of_bounds: created_at' using errcode = 'CS422';
end $$;

-- -----------------------------------------------------------------------------
-- 3. External workspace bindings (connections)
-- -----------------------------------------------------------------------------
create table if not exists public.cs_external_bindings (
  id                    uuid primary key default gen_random_uuid(),
  workspace_id          uuid not null references public.cs_workspaces(id),
  authority             public.cs_external_authority not null,
  -- service client registry (cs_private.cs_service_clients) is provisioned with CS-B5;
  -- null = binding verified by the configured gateway of this deployment.
  client_id             uuid,
  external_workspace_id text not null
    check (external_workspace_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
  status                text not null default 'active' check (status in ('active', 'revoked')),
  verification          jsonb not null check (jsonb_typeof(verification) = 'object'),
  verification_sha256   text not null check (verification_sha256 ~ '^[0-9a-f]{64}$'),
  created_by            uuid not null references auth.users(id),
  created_at            timestamptz not null default now(),
  revoked_at            timestamptz,
  unique (workspace_id, id),
  unique (workspace_id, authority),
  check ((status = 'revoked') = (revoked_at is not null))
);
create unique index if not exists cs_external_bindings_external_key
  on public.cs_external_bindings(authority, client_id, external_workspace_id) nulls not distinct;

create or replace function cs_private.cs_binding_guard() returns trigger
language plpgsql set search_path = public, pg_temp as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'cs_immutable_record: bindings cannot be deleted' using errcode = 'CS409';
  end if;
  if (new.id, new.workspace_id, new.authority, new.client_id, new.external_workspace_id, new.verification,
      new.verification_sha256, new.created_by, new.created_at)
     is distinct from
     (old.id, old.workspace_id, old.authority, old.client_id, old.external_workspace_id, old.verification,
      old.verification_sha256, old.created_by, old.created_at)
     or old.status = 'revoked' then
    raise exception 'cs_immutable_record: a binding can only be revoked' using errcode = 'CS409';
  end if;
  return new;
end $$;
drop trigger if exists cs_external_bindings_guard on public.cs_external_bindings;
create trigger cs_external_bindings_guard before update or delete on public.cs_external_bindings
  for each row execute function cs_private.cs_binding_guard();

-- -----------------------------------------------------------------------------
-- 4. Operations (durable pre-run owner) + exactly-one-owner work items
-- -----------------------------------------------------------------------------
create table if not exists public.cs_operations (
  id                    uuid primary key default gen_random_uuid(),
  workspace_id          uuid not null references public.cs_workspaces(id),
  environment_id        text not null references cs_private.cs_runtime_limits(environment_id),
  kind                  public.cs_operation_kind not null,
  state                 public.cs_operation_state not null default 'queued',
  created_by            uuid not null references auth.users(id),
  idempotency_key       uuid not null,
  request_sha256        text not null check (request_sha256 ~ '^[0-9a-f]{64}$'),
  event_sequence        bigint not null default 0 check (event_sequence >= 0),
  input_refs            jsonb not null check (jsonb_typeof(input_refs) = 'array' and jsonb_array_length(input_refs) between 1 and 100),
  result_refs           jsonb not null default '[]'::jsonb check (jsonb_typeof(result_refs) = 'array'),
  result_asset_refs     jsonb not null default '[]'::jsonb check (jsonb_typeof(result_asset_refs) = 'array'),
  authorization_receipt jsonb not null default '{}'::jsonb,
  cost                  jsonb,
  error                 jsonb,
  release_id            text not null,
  completed_at          timestamptz,
  admission             jsonb,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  unique (workspace_id, id),
  unique (workspace_id, kind, idempotency_key),
  -- compile/import have no paid admission or cost; authoring always has admission
  check (case when kind = 'treatment_authoring' then admission is not null
              else admission is null and cost is null end),
  check ((state = 'completed') = (completed_at is not null)),
  check (state <> 'completed' or jsonb_array_length(result_refs) > 0),
  check (state <> 'failed' or error is not null)
);

create table if not exists public.cs_operation_events (
  workspace_id uuid not null,
  operation_id uuid not null,
  sequence     bigint not null check (sequence > 0),
  kind         text not null check (kind ~ '^[a-z][a-z0-9_.]{0,63}$'),
  payload      jsonb not null default '{}'::jsonb,
  occurred_at  timestamptz not null default now(),
  primary key (workspace_id, operation_id, sequence),
  foreign key (workspace_id, operation_id) references public.cs_operations(workspace_id, id)
);

create or replace function cs_private.cs_operation_guard() returns trigger
language plpgsql set search_path = public, pg_temp as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'cs_immutable_record: operations cannot be deleted' using errcode = 'CS409';
  end if;
  if (new.id, new.workspace_id, new.environment_id, new.kind, new.created_by, new.idempotency_key,
      new.request_sha256, new.input_refs, new.admission, new.created_at)
     is distinct from
     (old.id, old.workspace_id, old.environment_id, old.kind, old.created_by, old.idempotency_key,
      old.request_sha256, old.input_refs, old.admission, old.created_at) then
    raise exception 'cs_immutable_record: operation identity and admitted request are immutable' using errcode = 'CS409';
  end if;
  if old.state in ('completed', 'failed', 'cancelled')
     and (new.state, new.result_refs, new.result_asset_refs, new.error, new.completed_at)
         is distinct from (old.state, old.result_refs, old.result_asset_refs, old.error, old.completed_at) then
    raise exception 'cs_immutable_record: a terminal operation cannot change' using errcode = 'CS409';
  end if;
  if old.result_refs <> '[]'::jsonb and new.result_refs is distinct from old.result_refs then
    raise exception 'cs_immutable_record: attached results are immutable' using errcode = 'CS409';
  end if;
  if new.event_sequence < old.event_sequence then
    raise exception 'cs_immutable_record: event sequence is monotonic' using errcode = 'CS409';
  end if;
  new.updated_at := now();
  return new;
end $$;
drop trigger if exists cs_operations_guard on public.cs_operations;
create trigger cs_operations_guard before update or delete on public.cs_operations
  for each row execute function cs_private.cs_operation_guard();

-- work items: exactly one owner (run+node XOR operation+step)
alter table public.cs_work_items add column if not exists operation_id uuid;
alter table public.cs_work_items add column if not exists operation_step_key text;
alter table public.cs_work_items alter column run_id drop not null;
alter table public.cs_work_items alter column node_id drop not null;
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'cs_work_items_operation_fk') then
    alter table public.cs_work_items add constraint cs_work_items_operation_fk
      foreign key (workspace_id, operation_id) references public.cs_operations(workspace_id, id);
  end if;
  if not exists (select 1 from pg_constraint where conname = 'cs_work_items_one_owner') then
    alter table public.cs_work_items add constraint cs_work_items_one_owner check (
      (run_id is not null and node_id is not null and operation_id is null and operation_step_key is null)
      or (run_id is null and node_id is null and operation_id is not null and operation_step_key is not null));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'cs_work_items_step_key') then
    alter table public.cs_work_items add constraint cs_work_items_step_key
      check (operation_step_key is null or operation_step_key ~ '^[a-z][a-z0-9_-]{0,63}$');
  end if;
end $$;

-- asset versions written by an operation-owned stage carry work_item_id without run/node
do $$
declare r record;
begin
  for r in select conname from pg_constraint
            where conrelid = 'public.cs_asset_versions'::regclass and contype = 'c'
              and pg_get_constraintdef(oid) like '%(run_id IS NULL) = (work_item_id IS NULL)%' loop
    execute format('alter table public.cs_asset_versions drop constraint %I', r.conname);
  end loop;
  if not exists (select 1 from pg_constraint where conname = 'cs_asset_versions_owner_check') then
    alter table public.cs_asset_versions add constraint cs_asset_versions_owner_check
      check ((run_id is null) = (node_id is null) and (run_id is null or work_item_id is not null));
  end if;
end $$;

-- brand files: SVG logos and WOFF2 fonts are retained as original bytes (§4/§5)
create or replace function cs_private.cs_extension_for_mime(p_mime text) returns text
language sql immutable set search_path = public, pg_temp as $$
  select case p_mime
           when 'image/png'     then 'png'
           when 'image/jpeg'    then 'jpg'
           when 'image/webp'    then 'webp'
           when 'image/svg+xml' then 'svg'
           when 'video/mp4'     then 'mp4'
           when 'audio/mp4'     then 'm4a'
           when 'font/ttf'      then 'ttf'
           when 'font/otf'      then 'otf'
           when 'font/woff2'    then 'woff2'
           when 'application/pdf' then 'pdf'
           else null
         end;
$$;
update storage.buckets
   set allowed_mime_types = (select array_agg(distinct m order by m)
                               from unnest(coalesce(allowed_mime_types, '{}') ||
                                           array['image/svg+xml', 'font/woff2', 'application/pdf']) m)
 where id = 'creative-studio';

create or replace function cs_private.cs_append_operation_event(
  p_workspace_id uuid, p_operation_id uuid, p_kind text, p_payload jsonb)
returns bigint
language plpgsql set search_path = public, pg_temp as $$
declare v_seq bigint;
begin
  update public.cs_operations o set event_sequence = o.event_sequence + 1
   where o.workspace_id = p_workspace_id and o.id = p_operation_id
  returning o.event_sequence into v_seq;               -- operation row lock serialises the sequence
  if v_seq is null then
    raise exception 'operation_missing' using errcode = 'CS404';
  end if;
  insert into public.cs_operation_events (workspace_id, operation_id, sequence, kind, payload)
  values (p_workspace_id, p_operation_id, v_seq, p_kind, coalesce(p_payload, '{}'::jsonb));
  return v_seq;
end $$;

-- -----------------------------------------------------------------------------
-- 5. Immutable approved source snapshots (brand packages) and asset mirrors
-- -----------------------------------------------------------------------------
create table if not exists public.cs_source_heads (
  workspace_id     uuid not null,
  binding_id       uuid not null,
  source_kind      text not null check (source_kind ~ '^[A-Za-z][A-Za-z0-9_-]{0,63}$'),
  current_version_id uuid not null,
  current_revision integer not null check (current_revision > 0),
  current_status   text not null check (current_status in ('approved', 'draft', 'stale', 'superseded', 'rejected', 'withdrawn')),
  observed_at      timestamptz not null default now(),
  observed_by      text not null check (length(observed_by) between 1 and 120),
  primary key (workspace_id, binding_id, source_kind),
  foreign key (workspace_id, binding_id) references public.cs_external_bindings(workspace_id, id)
);

create table if not exists public.cs_source_imports (
  workspace_id             uuid not null references public.cs_workspaces(id),
  object_id                uuid not null,
  version_id               uuid not null,
  revision                 integer not null check (revision > 0),
  kind                     text not null check (kind = 'brand_package'),
  authority                public.cs_external_authority not null,
  binding_id               uuid not null,
  source_ref               jsonb not null check (cs_private.cs_is_version_ref(source_ref)),
  source_version_id        uuid not null,
  source_revision          integer not null check (source_revision > 0),
  source_kind              text not null,
  payload                  jsonb not null check (jsonb_typeof(payload) = 'object'),
  sha256                   text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  approval_receipt_ref     jsonb not null check (jsonb_typeof(approval_receipt_ref) = 'object'),
  rights                   jsonb not null check (jsonb_typeof(rights) = 'array'),
  rights_expires_at        timestamptz,
  applicability_checked_at timestamptz not null,
  operation_id             uuid not null,
  created_at               timestamptz not null default now(),
  primary key (workspace_id, version_id),
  unique (workspace_id, object_id, revision),
  unique (workspace_id, binding_id, source_kind, source_version_id),
  foreign key (workspace_id, binding_id) references public.cs_external_bindings(workspace_id, id),
  foreign key (workspace_id, operation_id) references public.cs_operations(workspace_id, id),
  check (payload ->> 'sha256' = sha256 and payload ->> 'version_id' = version_id::text
         and payload ->> 'id' = object_id::text and (payload ->> 'revision')::int = revision),
  check (source_ref ->> 'version_id' = source_version_id::text and (source_ref ->> 'revision')::int = source_revision
         and source_ref ->> 'kind' = source_kind)
);

create table if not exists public.cs_source_asset_mirrors (
  workspace_id            uuid not null references public.cs_workspaces(id),
  source_authority        public.cs_external_authority not null,
  source_workspace_id     uuid not null,
  source_asset_id         uuid not null,
  source_asset_version_id uuid not null,
  source_sha256           text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  source_ref              jsonb not null,
  asset_id                uuid not null,
  asset_version_id        uuid not null,
  operation_id            uuid not null,
  created_at              timestamptz not null default now(),
  primary key (workspace_id, source_authority, source_workspace_id, source_asset_version_id, source_sha256),
  unique (workspace_id, asset_version_id),
  foreign key (workspace_id, asset_version_id) references public.cs_asset_versions(workspace_id, id),
  foreign key (workspace_id, operation_id) references public.cs_operations(workspace_id, id)
);

-- -----------------------------------------------------------------------------
-- 6. Creative requests, treatments, authoring drafts
-- -----------------------------------------------------------------------------
create table if not exists public.cs_creative_request_versions (
  workspace_id              uuid not null references public.cs_workspaces(id),
  object_id                 uuid not null,
  version_id                uuid not null,
  revision                  integer not null check (revision > 0),
  payload                   jsonb not null check (jsonb_typeof(payload) = 'object'),
  sha256                    text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  semantic_sha256           text not null check (semantic_sha256 ~ '^[0-9a-f]{64}$'),
  source_refs               jsonb not null check (jsonb_typeof(source_refs) = 'array'),
  brand_package_version_id  uuid not null,
  origin_kind               text not null check (origin_kind in ('standalone', 'marketing-os')),
  offering_kind             text not null check (offering_kind in ('physical_product', 'service', 'service_group',
                                                                   'digital_product', 'hybrid')),
  created_by                uuid not null references auth.users(id),
  created_at                timestamptz not null default now(),
  primary key (workspace_id, version_id),
  unique (workspace_id, object_id, revision),
  foreign key (workspace_id, brand_package_version_id) references public.cs_source_imports(workspace_id, version_id),
  check (payload ->> 'schema_version' = 'cs-creative-request-v1'),
  check (payload ->> 'sha256' = sha256 and payload ->> 'version_id' = version_id::text
         and payload ->> 'id' = object_id::text and (payload ->> 'revision')::int = revision
         and payload ->> 'workspace_id' = workspace_id::text and payload ->> 'created_by' = created_by::text),
  check (payload #>> '{origin,kind}' = origin_kind and payload #>> '{offering,kind}' = offering_kind),
  -- services carry no product geometry (contract §6)
  check (offering_kind not in ('service', 'service_group') or payload #> '{offering,physical_product_spec}' = 'null'::jsonb)
);

create table if not exists public.cs_treatment_versions (
  workspace_id              uuid not null references public.cs_workspaces(id),
  object_id                 uuid not null,
  version_id                uuid not null,
  revision                  integer not null check (revision > 0),
  payload                   jsonb not null check (jsonb_typeof(payload) = 'object'),
  sha256                    text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  semantic_sha256           text not null check (semantic_sha256 ~ '^[0-9a-f]{64}$'),
  source_refs               jsonb not null check (jsonb_typeof(source_refs) = 'array'),
  source_request_version_id uuid not null,
  state                     text not null check (state in ('draft', 'accepted', 'returned')),
  accepted_by               uuid references auth.users(id),
  accepted_at               timestamptz,
  source_operation_id       uuid,
  created_by                uuid not null references auth.users(id),
  created_at                timestamptz not null default now(),
  primary key (workspace_id, version_id),
  unique (workspace_id, object_id, revision),
  foreign key (workspace_id, source_request_version_id)
    references public.cs_creative_request_versions(workspace_id, version_id),
  foreign key (workspace_id, source_operation_id) references public.cs_operations(workspace_id, id),
  check (payload ->> 'schema_version' = 'cs-treatment-v1'),
  check (payload ->> 'sha256' = sha256 and payload ->> 'version_id' = version_id::text
         and payload ->> 'id' = object_id::text and (payload ->> 'revision')::int = revision
         and payload ->> 'workspace_id' = workspace_id::text and payload ->> 'status' = state
         and payload #>> '{source_request,version_id}' = source_request_version_id::text),
  check ((state = 'accepted') = (accepted_by is not null and accepted_at is not null)),
  check (state <> 'accepted' or (payload ->> 'accepted_by' = accepted_by::text)),
  check ((payload #>> '{authorship,kind}' = 'assistant') = (source_operation_id is not null))
);

create table if not exists public.cs_authoring_drafts (
  id                         uuid primary key,
  workspace_id               uuid not null references public.cs_workspaces(id),
  created_by                 uuid not null references auth.users(id),
  current_revision           integer not null check (current_revision > 0),
  sha256                     text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  payload                    jsonb not null check (jsonb_typeof(payload) = 'object'),
  promoted_recipe_version_id uuid,
  created_at                 timestamptz not null default now(),
  updated_at                 timestamptz not null default now(),
  unique (workspace_id, id),
  check (payload ->> 'schema_version' = 'cs-authoring-draft-v1'),
  check (payload ->> 'sha256' = sha256 and payload ->> 'id' = id::text
         and (payload ->> 'revision')::int = current_revision and payload ->> 'workspace_id' = workspace_id::text)
);

do $$
declare t text;
begin
  foreach t in array array['cs_operation_events', 'cs_source_imports', 'cs_source_asset_mirrors',
                           'cs_creative_request_versions', 'cs_treatment_versions'] loop
    execute format('drop trigger if exists %1$s_immutable on public.%1$s', t);
    execute format('create trigger %1$s_immutable before update or delete on public.%1$s for each row execute function cs_private.cs_reject_mutation()', t);
  end loop;
  foreach t in array array['cs_authoring_drafts'] loop
    execute format('drop trigger if exists %1$s_touch on public.%1$s', t);
    execute format('create trigger %1$s_touch before update on public.%1$s for each row execute function cs_private.cs_touch()', t);
  end loop;
end $$;

create or replace function cs_private.cs_draft_guard() returns trigger
language plpgsql set search_path = public, pg_temp as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'cs_immutable_record: drafts are archived, not deleted' using errcode = 'CS409';
  end if;
  if (new.id, new.workspace_id, new.created_by, new.created_at) is distinct from
     (old.id, old.workspace_id, old.created_by, old.created_at) then
    raise exception 'cs_immutable_record: draft identity is fixed' using errcode = 'CS409';
  end if;
  if new.current_revision <> old.current_revision + 1 then
    raise exception 'revision_conflict' using errcode = 'CS409';
  end if;
  if old.payload ->> 'status' <> 'editing' then
    raise exception 'draft_not_editable: % drafts cannot change', old.payload ->> 'status' using errcode = 'CS409';
  end if;
  return new;
end $$;
drop trigger if exists cs_authoring_drafts_guard on public.cs_authoring_drafts;
create trigger cs_authoring_drafts_guard before update or delete on public.cs_authoring_drafts
  for each row execute function cs_private.cs_draft_guard();

-- -----------------------------------------------------------------------------
-- 7. Brand eligibility (contract §4/§5) — authoritative, per action
-- -----------------------------------------------------------------------------
-- Actions: author (new request/draft pinning the package), generate, export,
-- handoff. Returns {decision, reason_codes, package_version_id, checked_at}.
-- Caller must already be scoped to the workspace (RLS/lease); unknown package
-- -> resource_unavailable.
create or replace function cs_private.cs_brand_eligibility(
  p_workspace_id uuid, p_package_version_id uuid, p_action text, p_channel text)
returns jsonb
language plpgsql stable set search_path = public, pg_temp as $$
declare
  v_pkg public.cs_source_imports;
  v_head public.cs_source_heads;
  v_binding public.cs_external_bindings;
  v_reasons text[] := array[]::text[];
  v_use text;
  v_now timestamptz := now();
  v_asset jsonb;
  v_rights jsonb;
  v_ref text;
  v_assets text[];
begin
  if p_action not in ('author', 'generate', 'export', 'handoff') then
    raise exception 'unsupported_action' using errcode = 'CS422';
  end if;
  select * into v_pkg from public.cs_source_imports s
   where s.workspace_id = p_workspace_id and s.version_id = p_package_version_id;
  if not found then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  select * into v_binding from public.cs_external_bindings b
   where b.workspace_id = p_workspace_id and b.id = v_pkg.binding_id;
  if v_binding.status <> 'active' then
    v_reasons := array_append(v_reasons, 'binding_revoked');
  end if;
  select * into v_head from public.cs_source_heads h
   where h.workspace_id = p_workspace_id and h.binding_id = v_pkg.binding_id and h.source_kind = v_pkg.source_kind;
  if not found or v_head.current_status in ('rejected', 'withdrawn', 'draft') and v_head.current_version_id = v_pkg.source_version_id then
    v_reasons := array_append(v_reasons, 'brand_not_approved');
  elsif v_head.current_version_id <> v_pkg.source_version_id or v_head.current_revision <> v_pkg.source_revision
        or v_head.current_status <> 'approved' then
    v_reasons := array_append(v_reasons, 'approval_stale');
  end if;

  if p_action in ('generate', 'export', 'handoff') then
    if exists (select 1 from jsonb_array_elements(v_pkg.payload -> 'unresolved_decisions') d
                where (d ->> 'blocks_compile')::boolean) then
      v_reasons := array_append(v_reasons, 'unresolved_decision_blocks');
    end if;
    -- which brand files the action uses, and the right it needs on each
    if p_action = 'generate' then
      v_use := 'reference';
      select coalesce(array_agg(r ->> 'asset_version_id'), '{}') into v_assets
        from jsonb_array_elements(v_pkg.payload #> '{imagery,reference_assets}') r;
    else
      v_use := case p_action when 'export' then 'embed' else 'redistribute' end;
      select coalesce(array_agg(x), '{}') into v_assets from (
        select l #>> '{asset,asset_version_id}' x from jsonb_array_elements(v_pkg.payload -> 'logos') l
        union
        select f ->> 'asset_version_id' from jsonb_array_elements(v_pkg.payload -> 'typography') t,
               jsonb_array_elements(t -> 'font_assets') f) s;
    end if;
    foreach v_ref in array v_assets loop
      v_asset := null;
      select a into v_asset from jsonb_array_elements(v_pkg.payload -> 'assets') a
       where a #>> '{ref,asset_version_id}' = v_ref;
      if v_asset is null then
        v_reasons := array_append(v_reasons, 'asset_unresolved');
        continue;
      end if;
      v_rights := v_asset -> 'rights';
      if v_rights ->> 'status' = 'denied' then
        v_reasons := array_append(v_reasons, 'rights_denied');
      elsif v_rights ->> 'status' = 'unknown' then
        v_reasons := array_append(v_reasons, 'rights_unknown');
      elsif (v_rights ->> 'valid_from')::timestamptz > v_now then
        v_reasons := array_append(v_reasons, 'rights_not_yet_valid');
      elsif v_rights ->> 'expires_at' is not null and (v_rights ->> 'expires_at')::timestamptz <= v_now then
        v_reasons := array_append(v_reasons, 'rights_expired');
      elsif not (v_rights -> 'allowed_uses') ? v_use then
        v_reasons := array_append(v_reasons, 'rights_use_not_covered');
      elsif p_channel is not null and not (v_rights -> 'channels' ? '*') and exists (
              select 1 from unnest(string_to_array(p_channel, ',')) c where not (v_rights -> 'channels') ? btrim(c)) then
        v_reasons := array_append(v_reasons, 'rights_channel_not_covered');
      end if;
    end loop;
  end if;
  select coalesce(array_agg(distinct r order by r), '{}') into v_reasons from unnest(v_reasons) r;
  return jsonb_build_object('decision', case when cardinality(v_reasons) = 0 then 'allowed' else 'refused' end,
                            'action', p_action, 'reason_codes', to_jsonb(v_reasons),
                            'package_version_id', v_pkg.version_id, 'checked_at', v_now);
end $$;

-- Raises the first typed refusal (stable precedence) when not allowed.
create or replace function cs_private.cs_require_brand_eligible(
  p_workspace_id uuid, p_package_version_id uuid, p_action text, p_channel text)
returns jsonb
language plpgsql stable set search_path = public, pg_temp as $$
declare
  v jsonb := cs_private.cs_brand_eligibility(p_workspace_id, p_package_version_id, p_action, p_channel);
  r jsonb := v -> 'reason_codes';
begin
  if v ->> 'decision' = 'allowed' then
    return v;
  end if;
  if r ? 'binding_revoked' then raise exception 'authorization_blocked: connection revoked' using errcode = 'CS403'; end if;
  if r ? 'brand_not_approved' then raise exception 'brand_not_approved' using errcode = 'CS409'; end if;
  if r ? 'approval_stale' then raise exception 'approval_stale' using errcode = 'CS409'; end if;
  if r ? 'unresolved_decision_blocks' then raise exception 'unresolved_decision_blocks' using errcode = 'CS409'; end if;
  if r ? 'rights_expired' then raise exception 'rights_expired' using errcode = 'CS403'; end if;
  raise exception 'rights_denied: %', array_to_string(array(select jsonb_array_elements_text(r)), ',') using errcode = 'CS403';
end $$;

-- -----------------------------------------------------------------------------
-- 8. Row level security
-- -----------------------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array['public.cs_external_bindings', 'public.cs_operations', 'public.cs_operation_events',
    'public.cs_source_heads', 'public.cs_source_imports', 'public.cs_source_asset_mirrors',
    'public.cs_creative_request_versions', 'public.cs_treatment_versions', 'public.cs_authoring_drafts'] loop
    execute format('alter table %s enable row level security', t);
    execute format('alter table %s force row level security', t);
    execute format('revoke all on table %s from public, anon, authenticated', t);
  end loop;
end $$;

do $$
declare r record;
begin
  for r in select schemaname, tablename, policyname from pg_policies
            where schemaname = 'public' and policyname like 'cs\_b2\_%' loop
    execute format('drop policy %I on %I.%I', r.policyname, r.schemaname, r.tablename);
  end loop;
end $$;

-- cs_api (signed-in user, current membership)
create policy cs_b2_api_select on public.cs_external_bindings for select to cs_api
  using (cs_private.cs_can(workspace_id, 'read'));
create policy cs_b2_api_insert on public.cs_external_bindings for insert to cs_api
  with check (cs_private.cs_can(workspace_id, 'manage') and created_by = cs_private.cs_actor_id() and status = 'active');
create policy cs_b2_api_update on public.cs_external_bindings for update to cs_api
  using (cs_private.cs_can(workspace_id, 'manage')) with check (cs_private.cs_can(workspace_id, 'manage'));

create policy cs_b2_api_select on public.cs_operations for select to cs_api
  using (cs_private.cs_can(workspace_id, 'read'));
create policy cs_b2_api_insert on public.cs_operations for insert to cs_api
  with check (cs_private.cs_can(workspace_id, 'produce') and created_by = cs_private.cs_actor_id()
              and state = 'queued' and kind = 'brand_package_import' and event_sequence = 1
              and result_refs = '[]'::jsonb and completed_at is null);
create policy cs_b2_api_select on public.cs_operation_events for select to cs_api
  using (cs_private.cs_can(workspace_id, 'read'));
create policy cs_b2_api_insert on public.cs_operation_events for insert to cs_api
  with check (cs_private.cs_can(workspace_id, 'produce') and sequence = 1 and kind = 'operation.admitted');
-- admission inserts the operation-owned work item (never a run/node item)
create policy cs_b2_api_insert on public.cs_work_items for insert to cs_api
  with check (operation_id is not null and run_id is null and state = 'queued' and fencing_token = 0
              and attempt_count = 0 and cs_private.cs_can(workspace_id, 'produce'));

create policy cs_b2_api_select on public.cs_source_heads for select to cs_api
  using (cs_private.cs_can(workspace_id, 'read'));
create policy cs_b2_api_insert on public.cs_source_heads for insert to cs_api
  with check (cs_private.cs_can(workspace_id, 'produce'));
create policy cs_b2_api_update on public.cs_source_heads for update to cs_api
  using (cs_private.cs_can(workspace_id, 'produce')) with check (cs_private.cs_can(workspace_id, 'produce'));

create policy cs_b2_api_select on public.cs_source_imports for select to cs_api
  using (cs_private.cs_can(workspace_id, 'read'));
create policy cs_b2_api_select on public.cs_source_asset_mirrors for select to cs_api
  using (cs_private.cs_can(workspace_id, 'read'));

create policy cs_b2_api_select on public.cs_creative_request_versions for select to cs_api
  using (cs_private.cs_can(workspace_id, 'read'));
create policy cs_b2_api_insert on public.cs_creative_request_versions for insert to cs_api
  with check (cs_private.cs_can(workspace_id, 'produce') and created_by = cs_private.cs_actor_id());
create policy cs_b2_api_select on public.cs_treatment_versions for select to cs_api
  using (cs_private.cs_can(workspace_id, 'read'));
create policy cs_b2_api_insert on public.cs_treatment_versions for insert to cs_api
  with check (cs_private.cs_can(workspace_id, 'produce') and created_by = cs_private.cs_actor_id()
              and source_operation_id is null);
create policy cs_b2_api_select on public.cs_authoring_drafts for select to cs_api
  using (cs_private.cs_can(workspace_id, 'read'));
create policy cs_b2_api_insert on public.cs_authoring_drafts for insert to cs_api
  with check (cs_private.cs_can(workspace_id, 'produce') and created_by = cs_private.cs_actor_id()
              and promoted_recipe_version_id is null);
create policy cs_b2_api_update on public.cs_authoring_drafts for update to cs_api
  using (cs_private.cs_can(workspace_id, 'produce'))
  with check (cs_private.cs_can(workspace_id, 'produce') and promoted_recipe_version_id is null);

-- cs_worker (import stage). No membership, no drafts/requests/treatments.
create policy cs_b2_worker_all on public.cs_operations for all to cs_worker using (true) with check (true);
create policy cs_b2_worker_all on public.cs_operation_events for all to cs_worker using (true) with check (true);
create policy cs_b2_worker_select on public.cs_external_bindings for select to cs_worker using (true);
create policy cs_b2_worker_all on public.cs_source_heads for all to cs_worker using (true) with check (true);
create policy cs_b2_worker_all on public.cs_source_imports for all to cs_worker using (true) with check (true);
create policy cs_b2_worker_all on public.cs_source_asset_mirrors for all to cs_worker using (true) with check (true);

-- -----------------------------------------------------------------------------
-- 9. Table grants
-- -----------------------------------------------------------------------------
grant select, insert on public.cs_external_bindings to cs_api;
grant update (status, revoked_at) on public.cs_external_bindings to cs_api;
grant select, insert on public.cs_operations, public.cs_operation_events to cs_api;
grant insert (id, workspace_id, environment_id, operation_id, operation_step_key, stage, stage_revision, dedupe_key,
              due_at, state, attempt_count, max_attempts, fencing_token, payload_ref, deadline_at)
  on public.cs_work_items to cs_api;
grant select, insert on public.cs_source_heads to cs_api;
grant update (current_version_id, current_revision, current_status, observed_at, observed_by) on public.cs_source_heads to cs_api;
grant select on public.cs_source_imports, public.cs_source_asset_mirrors to cs_api;
grant select, insert on public.cs_creative_request_versions, public.cs_treatment_versions to cs_api;
grant select, insert on public.cs_authoring_drafts to cs_api;
grant update (current_revision, sha256, payload, updated_at) on public.cs_authoring_drafts to cs_api;

grant select on public.cs_operations to cs_worker;
grant update (state, event_sequence, result_refs, result_asset_refs, error, completed_at, updated_at)
  on public.cs_operations to cs_worker;
grant select, insert on public.cs_operation_events to cs_worker;
grant select on public.cs_external_bindings to cs_worker;
grant select, insert on public.cs_source_heads to cs_worker;
grant update (current_version_id, current_revision, current_status, observed_at, observed_by) on public.cs_source_heads to cs_worker;
grant select, insert on public.cs_source_imports, public.cs_source_asset_mirrors to cs_worker;

-- -----------------------------------------------------------------------------
-- 10. API procedures (SECURITY INVOKER: run as cs_api under RLS)
-- -----------------------------------------------------------------------------
create or replace function cs_private.cs_require_produce(p_workspace_id uuid) returns uuid
language plpgsql stable set search_path = public, pg_temp as $$
declare v_actor uuid := cs_private.cs_require_actor();
begin
  if not cs_private.cs_can(p_workspace_id, 'read') then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  if not cs_private.cs_can(p_workspace_id, 'produce') then
    raise exception 'capability_denied' using errcode = 'CS403';
  end if;
  return v_actor;
end $$;

create or replace function cs_private.cs_audit(p_workspace_id uuid, p_actor uuid, p_action text, p_input text, p_refs jsonb)
returns void
language sql set search_path = public, pg_temp as $$
  insert into public.cs_audit_events (account_id, workspace_id, actor_id, action, input_sha256, affected_refs)
  select w.account_id, p_workspace_id, p_actor, p_action, p_input, p_refs
    from public.cs_workspaces w where w.id = p_workspace_id;
$$;

-- The API has verified the binding with the external authority's own API
-- (p_verification is that authority's response); SQL checks it names this
-- exact external workspace and grants the binding action, and records it.
create or replace function public.cs_bind_external_workspace(
  p_workspace_id uuid, p_authority public.cs_external_authority, p_external_workspace_id text,
  p_verification jsonb, p_idempotency_key uuid, p_request_sha256 text)
returns table (binding_id uuid, replayed boolean)
language plpgsql set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_require_actor();
  v_prior jsonb;
  v_id uuid := gen_random_uuid();
begin
  if not cs_private.cs_can(p_workspace_id, 'read') then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  if not cs_private.cs_can(p_workspace_id, 'manage') then
    raise exception 'capability_denied' using errcode = 'CS403';
  end if;
  v_prior := cs_private.cs_idempotency_replay(p_workspace_id, 'connection.create', p_idempotency_key, p_request_sha256);
  if v_prior is not null then
    return query select (v_prior ->> 'binding_id')::uuid, true;
    return;
  end if;
  if p_verification is null or jsonb_typeof(p_verification) <> 'object'
     or p_verification ->> 'authority' is distinct from p_authority::text
     or p_verification ->> 'external_workspace_id' is distinct from p_external_workspace_id
     or p_verification ->> 'decision' is distinct from 'allowed'
     or p_verification ->> 'subject_user_id' is distinct from v_actor::text then
    raise exception 'authorization_blocked: external authority did not authorise this binding' using errcode = 'CS403';
  end if;
  insert into public.cs_external_bindings (id, workspace_id, authority, external_workspace_id, verification,
                                           verification_sha256, created_by)
  values (v_id, p_workspace_id, p_authority, p_external_workspace_id, p_verification,
          encode(sha256(convert_to(p_verification::text, 'UTF8')), 'hex'), v_actor);
  insert into public.cs_idempotency (workspace_id, actor_id, operation, key, request_sha256, result_ref)
  values (p_workspace_id, v_actor, 'connection.create', p_idempotency_key, p_request_sha256,
          jsonb_build_object('binding_id', v_id));
  perform cs_private.cs_audit(p_workspace_id, v_actor, 'connection.create', p_request_sha256,
    jsonb_build_array(jsonb_build_object('kind', 'external_binding', 'id', v_id, 'authority', p_authority)));
  return query select v_id, false;
exception when unique_violation then
  raise exception 'binding_exists: this workspace or external workspace is already connected' using errcode = 'CS409';
end $$;

-- Records the upstream approval state the API just read from the authority's
-- API (connection refresh). Revision never moves backwards.
create or replace function public.cs_record_source_head(
  p_workspace_id uuid, p_binding_id uuid, p_source_kind text, p_version_id uuid, p_revision integer, p_status text)
returns void
language plpgsql set search_path = public, pg_temp as $$
declare v_actor uuid := cs_private.cs_require_produce(p_workspace_id);
begin
  if not exists (select 1 from public.cs_external_bindings b where b.workspace_id = p_workspace_id
                   and b.id = p_binding_id and b.status = 'active') then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  perform cs_private.cs_upsert_source_head(p_workspace_id, p_binding_id, p_source_kind, p_version_id, p_revision,
                                           p_status, 'actor:' || v_actor::text);
end $$;

create or replace function cs_private.cs_upsert_source_head(
  p_workspace_id uuid, p_binding_id uuid, p_source_kind text, p_version_id uuid, p_revision integer, p_status text,
  p_observed_by text)
returns void
language plpgsql set search_path = public, pg_temp as $$
declare v_old public.cs_source_heads;
begin
  select * into v_old from public.cs_source_heads h
   where h.workspace_id = p_workspace_id and h.binding_id = p_binding_id and h.source_kind = p_source_kind for update;
  if found and p_revision < v_old.current_revision then
    raise exception 'source_revision_regressed' using errcode = 'CS409';
  end if;
  insert into public.cs_source_heads as h (workspace_id, binding_id, source_kind, current_version_id, current_revision,
                                           current_status, observed_at, observed_by)
  values (p_workspace_id, p_binding_id, p_source_kind, p_version_id, p_revision, p_status, now(), p_observed_by)
  on conflict (workspace_id, binding_id, source_kind) do update
    set current_version_id = excluded.current_version_id, current_revision = excluded.current_revision,
        current_status = excluded.current_status, observed_at = excluded.observed_at, observed_by = excluded.observed_by;
end $$;

-- 202 admission of an exact brand package import (database-contract cs_admit_brand_import).
create or replace function public.cs_admit_brand_import(
  p_workspace_id uuid, p_binding_id uuid, p_package_ref jsonb, p_environment_id text, p_release_id text,
  p_idempotency_key uuid, p_request_sha256 text)
returns table (operation_id uuid, state public.cs_operation_state, replayed boolean)
language plpgsql set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_require_produce(p_workspace_id);
  v_prior jsonb;
  v_binding public.cs_external_bindings;
  v_limits record;
  v_op uuid := gen_random_uuid();
begin
  v_prior := cs_private.cs_idempotency_replay(p_workspace_id, 'brand_package.import', p_idempotency_key, p_request_sha256);
  if v_prior is not null then
    return query select o.id, o.state, true from public.cs_operations o
      where o.workspace_id = p_workspace_id and o.id = (v_prior ->> 'operation_id')::uuid;
    return;
  end if;
  select * into v_limits from cs_private.cs_runtime_limits l where l.environment_id = p_environment_id;
  if not found then
    raise exception 'environment_unavailable' using errcode = 'CS503';
  end if;
  if not v_limits.admission_enabled then
    raise exception 'admission_disabled' using errcode = 'CS503';
  end if;
  select * into v_binding from public.cs_external_bindings b where b.workspace_id = p_workspace_id and b.id = p_binding_id;
  if not found then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  if v_binding.status <> 'active' then
    raise exception 'authorization_blocked: connection revoked' using errcode = 'CS403';
  end if;
  if not cs_private.cs_is_version_ref(p_package_ref) or p_package_ref ->> 'kind' not in ('BrandKit', 'brand-package') then
    raise exception 'invalid_package_ref' using errcode = 'CS422';
  end if;
  if p_package_ref ->> 'authority' <> v_binding.authority::text
     or p_package_ref ->> 'workspace_id' <> v_binding.external_workspace_id then
    raise exception 'scope_mismatch: package is not in the connected workspace' using errcode = 'CS403';
  end if;
  insert into public.cs_operations (id, workspace_id, environment_id, kind, state, created_by, idempotency_key,
    request_sha256, event_sequence, input_refs, authorization_receipt, release_id)
  values (v_op, p_workspace_id, p_environment_id, 'brand_package_import', 'queued', v_actor, p_idempotency_key,
          p_request_sha256, 1, jsonb_build_array(p_package_ref),
          jsonb_build_object('binding_id', v_binding.id, 'authority', v_binding.authority,
                             'verification_sha256', v_binding.verification_sha256), p_release_id);
  insert into public.cs_operation_events (workspace_id, operation_id, sequence, kind, payload)
  values (p_workspace_id, v_op, 1, 'operation.admitted',
          jsonb_build_object('kind', 'brand_package_import', 'package', p_package_ref));
  insert into public.cs_work_items (workspace_id, environment_id, operation_id, operation_step_key, stage,
                                    dedupe_key, max_attempts, payload_ref)
  values (p_workspace_id, p_environment_id, v_op, 'import', 'import_brand_package', 'op:' || v_op::text || ':import', 3,
          jsonb_build_object('binding_id', v_binding.id, 'package_ref', p_package_ref));
  insert into public.cs_idempotency (workspace_id, actor_id, operation, key, request_sha256, result_ref)
  values (p_workspace_id, v_actor, 'brand_package.import', p_idempotency_key, p_request_sha256,
          jsonb_build_object('operation_id', v_op));
  perform cs_private.cs_audit(p_workspace_id, v_actor, 'brand_package.import', p_request_sha256,
    jsonb_build_array(jsonb_build_object('kind', 'operation', 'id', v_op)));
  return query select v_op, 'queued'::public.cs_operation_state, false;
end $$;

create or replace function public.cs_brand_package_eligibility(
  p_workspace_id uuid, p_package_version_id uuid, p_action text, p_channel text)
returns jsonb
language plpgsql stable set search_path = public, pg_temp as $$
begin
  perform cs_private.cs_require_actor();
  if not cs_private.cs_can(p_workspace_id, 'read') then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  return cs_private.cs_brand_eligibility(p_workspace_id, p_package_version_id, p_action, p_channel);
end $$;

-- Resolves a VersionRef to an object saved in this workspace with the same digest.
create or replace function cs_private.cs_resolve_local_ref(p_workspace_id uuid, p_ref jsonb, p_table text)
returns boolean
language plpgsql stable set search_path = public, pg_temp as $$
declare v_found boolean;
begin
  if not cs_private.cs_is_version_ref(p_ref) then
    return false;
  end if;
  execute format('select exists (select 1 from public.%I t where t.workspace_id = $1 and t.version_id = ($2 ->> ''version_id'')::uuid
                    and t.object_id = ($2 ->> ''id'')::uuid and t.revision = ($2 ->> ''revision'')::int and t.sha256 = $2 ->> ''sha256'')',
                 p_table) into v_found using p_workspace_id, p_ref;
  return v_found;
end $$;

create or replace function public.cs_save_creative_request(
  p_workspace_id uuid, p_document jsonb, p_canonical text, p_semantic_sha256 text, p_if_match text,
  p_idempotency_key uuid, p_request_sha256 text)
returns table (version_id uuid, sha256 text, replayed boolean)
language plpgsql set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_require_produce(p_workspace_id);
  v_prior jsonb;
  v_sha text;
  v_object uuid;
  v_rev integer;
  v_head record;
  v_pkg public.cs_source_imports;
  v_origin jsonb;
  v_elig jsonb;
begin
  v_prior := cs_private.cs_idempotency_replay(p_workspace_id, 'creative_request.save', p_idempotency_key, p_request_sha256);
  if v_prior is not null then
    return query select (v_prior ->> 'version_id')::uuid, v_prior ->> 'sha256', true;
    return;
  end if;
  v_sha := cs_private.cs_assert_document_digest(p_document, p_canonical);
  perform cs_private.cs_assert_audit(p_document, p_workspace_id, v_actor);
  if p_semantic_sha256 is null or p_semantic_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid_semantic_digest' using errcode = 'CS422';
  end if;
  v_object := (p_document ->> 'id')::uuid;
  v_rev := (p_document ->> 'revision')::int;
  select r.revision, r.sha256 into v_head from public.cs_creative_request_versions r
   where r.workspace_id = p_workspace_id and r.object_id = v_object order by r.revision desc limit 1;
  if found then
    if p_if_match is distinct from v_head.sha256 or v_rev <> v_head.revision + 1 then
      raise exception 'revision_conflict' using errcode = 'CS409';
    end if;
  elsif v_rev <> 1 or p_if_match is not null then
    raise exception 'revision_conflict' using errcode = 'CS409';
  end if;

  -- brand package: exact imported version in THIS workspace, currently eligible to author against
  select * into v_pkg from public.cs_source_imports s
   where s.workspace_id = p_workspace_id and s.version_id = (p_document #>> '{brand_package,version_id}')::uuid;
  if not found then
    raise exception 'resource_unavailable: brand package' using errcode = 'CS404';
  end if;
  if not cs_private.cs_is_version_ref(p_document -> 'brand_package')
     or p_document #>> '{brand_package,sha256}' <> v_pkg.sha256
     or p_document #>> '{brand_package,id}' <> v_pkg.object_id::text
     or (p_document #>> '{brand_package,revision}')::int <> v_pkg.revision then
    raise exception 'source_revision_changed: brand package ref does not match the imported version' using errcode = 'CS409';
  end if;
  v_elig := cs_private.cs_require_brand_eligible(p_workspace_id, v_pkg.version_id, 'author', null);

  v_origin := p_document -> 'origin';
  if v_origin ->> 'kind' = 'marketing-os' then
    if not exists (select 1 from public.cs_external_bindings b
                    where b.workspace_id = p_workspace_id and b.authority = 'marketing-os' and b.status = 'active'
                      and b.external_workspace_id = v_origin ->> 'mos_workspace_id') then
      raise exception 'scope_mismatch: marketing-os workspace is not connected' using errcode = 'CS403';
    end if;
    if v_origin #>> '{generation_eligibility,decision}' <> 'allowed'
       or v_origin #>> '{generation_eligibility,action}' <> 'generate'
       or v_origin #>> '{generation_eligibility,workspace_id}' <> v_origin ->> 'mos_workspace_id'
       or (v_origin #>> '{generation_eligibility,valid_until}')::timestamptz
          > (v_origin #>> '{generation_eligibility,checked_at}')::timestamptz + interval '60 seconds'
       or v_origin #>> '{concept_approval,decision}' <> 'approved'
       or v_origin #>> '{concept_approval,subject,version_id}' <> v_origin #>> '{concept,version_id}' then
      raise exception 'authorization_blocked: marketing-os approval/eligibility evidence does not cover this request'
        using errcode = 'CS403';
    end if;
  end if;

  insert into public.cs_creative_request_versions (workspace_id, object_id, version_id, revision, payload, sha256,
    semantic_sha256, source_refs, brand_package_version_id, origin_kind, offering_kind, created_by)
  values (p_workspace_id, v_object, (p_document ->> 'version_id')::uuid, v_rev, p_document, v_sha, p_semantic_sha256,
          jsonb_build_array(p_document -> 'brand_package', p_document #> '{offering,source}')
            || coalesce(p_document -> 'audiences', '[]'::jsonb),
          v_pkg.version_id, v_origin ->> 'kind', p_document #>> '{offering,kind}', v_actor);
  insert into public.cs_idempotency (workspace_id, actor_id, operation, key, request_sha256, result_ref)
  values (p_workspace_id, v_actor, 'creative_request.save', p_idempotency_key, p_request_sha256,
          jsonb_build_object('version_id', p_document ->> 'version_id', 'sha256', v_sha));
  perform cs_private.cs_audit(p_workspace_id, v_actor, 'creative_request.save', p_request_sha256,
    jsonb_build_array(jsonb_build_object('kind', 'creative_request', 'id', v_object, 'version_id', p_document ->> 'version_id')));
  return query select (p_document ->> 'version_id')::uuid, v_sha, false;
exception when unique_violation then
  raise exception 'revision_conflict' using errcode = 'CS409';
end $$;

-- Human/legacy draft treatment revision (no model call). Assistant drafts are
-- inserted only by the paid treatment_authoring operation (CS-B3).
create or replace function public.cs_save_treatment(
  p_workspace_id uuid, p_document jsonb, p_canonical text, p_semantic_sha256 text, p_if_match text,
  p_idempotency_key uuid, p_request_sha256 text)
returns table (version_id uuid, sha256 text, replayed boolean)
language plpgsql set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_require_produce(p_workspace_id);
  v_prior jsonb;
  v_sha text;
  v_object uuid;
  v_rev integer;
  v_head record;
  v_req record;
begin
  v_prior := cs_private.cs_idempotency_replay(p_workspace_id, 'treatment.save', p_idempotency_key, p_request_sha256);
  if v_prior is not null then
    return query select (v_prior ->> 'version_id')::uuid, v_prior ->> 'sha256', true;
    return;
  end if;
  v_sha := cs_private.cs_assert_document_digest(p_document, p_canonical);
  perform cs_private.cs_assert_audit(p_document, p_workspace_id, v_actor);
  if p_semantic_sha256 is null or p_semantic_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid_semantic_digest' using errcode = 'CS422';
  end if;
  if p_document ->> 'status' <> 'draft' or p_document -> 'accepted_by' <> 'null'::jsonb
     or p_document -> 'accepted_at' <> 'null'::jsonb then
    raise exception 'treatment_not_draft: acceptance is a separate explicit action' using errcode = 'CS422';
  end if;
  if p_document #>> '{authorship,kind}' not in ('human', 'legacy_import') then
    raise exception 'authorship_requires_operation: assistant drafts come from a treatment_authoring operation'
      using errcode = 'CS422';
  end if;
  if not cs_private.cs_resolve_local_ref(p_workspace_id, p_document -> 'source_request', 'cs_creative_request_versions') then
    raise exception 'resource_unavailable: source request' using errcode = 'CS404';
  end if;
  v_object := (p_document ->> 'id')::uuid;
  v_rev := (p_document ->> 'revision')::int;
  select t.revision, t.sha256, t.source_request_version_id into v_head from public.cs_treatment_versions t
   where t.workspace_id = p_workspace_id and t.object_id = v_object order by t.revision desc limit 1;
  if found then
    if p_if_match is distinct from v_head.sha256 or v_rev <> v_head.revision + 1 then
      raise exception 'revision_conflict' using errcode = 'CS409';
    end if;
  elsif v_rev <> 1 or p_if_match is not null then
    raise exception 'revision_conflict' using errcode = 'CS409';
  end if;
  insert into public.cs_treatment_versions (workspace_id, object_id, version_id, revision, payload, sha256, semantic_sha256,
    source_refs, source_request_version_id, state, created_by)
  values (p_workspace_id, v_object, (p_document ->> 'version_id')::uuid, v_rev, p_document, v_sha, p_semantic_sha256,
          jsonb_build_array(p_document -> 'source_request') || coalesce(p_document -> 'sources', '[]'::jsonb),
          (p_document #>> '{source_request,version_id}')::uuid, 'draft', v_actor);
  insert into public.cs_idempotency (workspace_id, actor_id, operation, key, request_sha256, result_ref)
  values (p_workspace_id, v_actor, 'treatment.save', p_idempotency_key, p_request_sha256,
          jsonb_build_object('version_id', p_document ->> 'version_id', 'sha256', v_sha));
  perform cs_private.cs_audit(p_workspace_id, v_actor, 'treatment.save', p_request_sha256,
    jsonb_build_array(jsonb_build_object('kind', 'treatment', 'id', v_object, 'version_id', p_document ->> 'version_id')));
  return query select (p_document ->> 'version_id')::uuid, v_sha, false;
exception when unique_violation then
  raise exception 'revision_conflict' using errcode = 'CS409';
end $$;

-- Explicit acceptance: freezes the current draft's direction/shots/sources into
-- a new accepted revision carrying the accepting actor and time.
create or replace function public.cs_accept_treatment(
  p_workspace_id uuid, p_document jsonb, p_canonical text, p_semantic_sha256 text, p_if_match text,
  p_idempotency_key uuid, p_request_sha256 text)
returns table (version_id uuid, sha256 text, replayed boolean)
language plpgsql set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_require_produce(p_workspace_id);
  v_prior jsonb;
  v_sha text;
  v_draft public.cs_treatment_versions;
begin
  v_prior := cs_private.cs_idempotency_replay(p_workspace_id, 'treatment.accept', p_idempotency_key, p_request_sha256);
  if v_prior is not null then
    return query select (v_prior ->> 'version_id')::uuid, v_prior ->> 'sha256', true;
    return;
  end if;
  v_sha := cs_private.cs_assert_document_digest(p_document, p_canonical);
  if (p_document ->> 'workspace_id') is distinct from p_workspace_id::text then
    raise exception 'scope_mismatch: document workspace' using errcode = 'CS403';
  end if;
  select * into v_draft from public.cs_treatment_versions t
   where t.workspace_id = p_workspace_id and t.object_id = (p_document ->> 'id')::uuid
   order by t.revision desc limit 1;
  if not found then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  if p_if_match is distinct from v_draft.sha256 or (p_document ->> 'revision')::int <> v_draft.revision + 1 then
    raise exception 'revision_conflict' using errcode = 'CS409';
  end if;
  if v_draft.state <> 'draft' then
    raise exception 'treatment_not_draft: only the current draft revision can be accepted' using errcode = 'CS409';
  end if;
  if p_document ->> 'status' <> 'accepted' or p_document ->> 'accepted_by' <> v_actor::text
     or abs(extract(epoch from ((p_document ->> 'accepted_at')::timestamptz - now()))) > 300
     or p_document ->> 'created_by' <> v_actor::text
     or abs(extract(epoch from ((p_document ->> 'created_at')::timestamptz - now()))) > 300 then
    raise exception 'acceptance_invalid: accepted_by/at must be the signed-in user now' using errcode = 'CS422';
  end if;
  -- frozen content is exactly the reviewed draft
  if (p_document -> 'direction', p_document -> 'shots', p_document -> 'sources', p_document -> 'source_request',
      p_document -> 'authorship')
     is distinct from
     (v_draft.payload -> 'direction', v_draft.payload -> 'shots', v_draft.payload -> 'sources',
      v_draft.payload -> 'source_request', v_draft.payload -> 'authorship')
     or p_semantic_sha256 is distinct from v_draft.semantic_sha256 then
    raise exception 'acceptance_invalid: accepted content differs from the reviewed draft' using errcode = 'CS409';
  end if;
  insert into public.cs_treatment_versions (workspace_id, object_id, version_id, revision, payload, sha256, semantic_sha256,
    source_refs, source_request_version_id, state, accepted_by, accepted_at, source_operation_id, created_by)
  values (p_workspace_id, v_draft.object_id, (p_document ->> 'version_id')::uuid, v_draft.revision + 1, p_document, v_sha,
          v_draft.semantic_sha256, v_draft.source_refs, v_draft.source_request_version_id, 'accepted', v_actor,
          (p_document ->> 'accepted_at')::timestamptz, v_draft.source_operation_id, v_actor);
  insert into public.cs_idempotency (workspace_id, actor_id, operation, key, request_sha256, result_ref)
  values (p_workspace_id, v_actor, 'treatment.accept', p_idempotency_key, p_request_sha256,
          jsonb_build_object('version_id', p_document ->> 'version_id', 'sha256', v_sha));
  perform cs_private.cs_audit(p_workspace_id, v_actor, 'treatment.accept', p_request_sha256,
    jsonb_build_array(jsonb_build_object('kind', 'treatment', 'id', v_draft.object_id, 'version_id', p_document ->> 'version_id')));
  return query select (p_document ->> 'version_id')::uuid, v_sha, false;
exception when unique_violation then
  raise exception 'revision_conflict' using errcode = 'CS409';
end $$;

-- Non-executable authoring draft: create (expected revision 0) or save (current).
create or replace function public.cs_save_authoring_draft(
  p_workspace_id uuid, p_document jsonb, p_canonical text, p_expected_revision integer)
returns table (draft_id uuid, revision integer, sha256 text)
language plpgsql set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_require_produce(p_workspace_id);
  v_sha text;
  v_id uuid;
  v_sel jsonb;
  v_ref jsonb;
begin
  v_sha := cs_private.cs_assert_document_digest(p_document, p_canonical);
  if octet_length(p_canonical) > 262144 then
    raise exception 'draft_too_large' using errcode = 'CS422';
  end if;
  if (p_document ->> 'workspace_id') is distinct from p_workspace_id::text then
    raise exception 'scope_mismatch: document workspace' using errcode = 'CS403';
  end if;
  if p_document ->> 'status' = 'promoted' or p_document -> 'promoted_recipe' <> 'null'::jsonb then
    raise exception 'promotion_not_available: promotion is a separate atomic recipe operation' using errcode = 'CS422';
  end if;
  -- every selected source must be an exact saved version in this workspace
  v_sel := p_document -> 'selections';
  if v_sel -> 'brand_package' <> 'null'::jsonb
     and not cs_private.cs_resolve_local_ref(p_workspace_id, v_sel -> 'brand_package', 'cs_source_imports') then
    raise exception 'resource_unavailable: selected brand package' using errcode = 'CS404';
  end if;
  if v_sel -> 'request' <> 'null'::jsonb
     and not cs_private.cs_resolve_local_ref(p_workspace_id, v_sel -> 'request', 'cs_creative_request_versions') then
    raise exception 'resource_unavailable: selected request' using errcode = 'CS404';
  end if;
  if v_sel -> 'treatment' <> 'null'::jsonb
     and not cs_private.cs_resolve_local_ref(p_workspace_id, v_sel -> 'treatment', 'cs_treatment_versions') then
    raise exception 'resource_unavailable: selected treatment' using errcode = 'CS404';
  end if;
  v_id := (p_document ->> 'id')::uuid;
  if p_expected_revision = 0 then
    perform cs_private.cs_assert_audit(p_document, p_workspace_id, v_actor);
    if (p_document ->> 'revision')::int <> 1 then
      raise exception 'revision_conflict' using errcode = 'CS409';
    end if;
    insert into public.cs_authoring_drafts (id, workspace_id, created_by, current_revision, sha256, payload)
    values (v_id, p_workspace_id, v_actor, 1, v_sha, p_document);
  else
    update public.cs_authoring_drafts d
       set current_revision = d.current_revision + 1, sha256 = v_sha, payload = p_document
     where d.workspace_id = p_workspace_id and d.id = v_id and d.current_revision = p_expected_revision
       and (p_document ->> 'revision')::int = p_expected_revision + 1
       and p_document ->> 'created_by' = d.created_by::text
       and p_document ->> 'created_at' = d.payload ->> 'created_at';
    if not found then
      if not exists (select 1 from public.cs_authoring_drafts d where d.workspace_id = p_workspace_id and d.id = v_id) then
        raise exception 'resource_unavailable' using errcode = 'CS404';
      end if;
      raise exception 'revision_conflict' using errcode = 'CS409';
    end if;
  end if;
  return query select v_id, (p_document ->> 'revision')::int, v_sha;
exception when unique_violation then
  raise exception 'revision_conflict' using errcode = 'CS409';
end $$;

-- -----------------------------------------------------------------------------
-- 11. Worker: operation-aware scheduling (replaces the CS-B1 bodies; run/node
--     behaviour is unchanged, operation/step owners get their own event log)
-- -----------------------------------------------------------------------------
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
         -- an operation that is already terminal gets no further work
         and (w.operation_id is null or exists (select 1 from public.cs_operations o
                where o.workspace_id = w.workspace_id and o.id = w.operation_id
                  and o.state not in ('completed', 'failed', 'cancelled')))
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
    if v_item.run_id is not null then
      update public.cs_run_nodes n set state = 'running', attempt_number = v_item.attempt_count
       where n.workspace_id = v_item.workspace_id and n.run_id = v_item.run_id and n.node_id = v_item.node_id;
      update public.cs_runs r set state = 'running'
       where r.workspace_id = v_item.workspace_id and r.id = v_item.run_id and r.state = 'queued';
      perform cs_private.cs_append_run_event(v_item.workspace_id, v_item.run_id, 'work.claimed',
        jsonb_build_object('node_id', v_item.node_id, 'stage', v_item.stage, 'fencing_token', v_item.fencing_token));
    else
      update public.cs_operations o set state = 'running'
       where o.workspace_id = v_item.workspace_id and o.id = v_item.operation_id and o.state = 'queued';
      perform cs_private.cs_append_operation_event(v_item.workspace_id, v_item.operation_id, 'work.claimed',
        jsonb_build_object('step', v_item.operation_step_key, 'stage', v_item.stage, 'fencing_token', v_item.fencing_token));
    end if;
    work_item_id := v_item.id; workspace_id := v_item.workspace_id; run_id := v_item.run_id;
    node_id := v_item.node_id; stage := v_item.stage; stage_revision := v_item.stage_revision;
    payload_ref := v_item.payload_ref; attempt_count := v_item.attempt_count;
    lease_token := v_item.lease_token; fencing_token := v_item.fencing_token; lease_until := v_item.lease_until;
    return next;
  end loop;
end $$;

create or replace function public.cs_finish_work(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_result_kind text,
  p_input_sha256 text, p_output_refs jsonb, p_receipt_sha256 text, p_release_id text)
returns uuid
language plpgsql set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
  v_op public.cs_operations;
  v_result uuid;
  v_seq bigint;
begin
  v := cs_private.cs_lock_live_lease(p_work_item_id, p_lease_token, p_fencing_token);
  perform cs_private.cs_assert_asset_refs(v.workspace_id, p_output_refs);
  if v.operation_id is not null then
    select * into v_op from public.cs_operations o where o.workspace_id = v.workspace_id and o.id = v.operation_id for update;
    -- complete only after the stage attached exact immutable results, or record the
    -- stage's own typed failure; never mark an operation done without either
    if v_op.state <> 'failed' and jsonb_array_length(v_op.result_refs) = 0 then
      raise exception 'operation_result_missing' using errcode = 'CS409';
    end if;
  end if;
  insert into public.cs_stage_results (workspace_id, work_item_id, fencing_token, result_kind, input_sha256,
    output_refs, receipt_sha256, release_id)
  values (v.workspace_id, v.id, v.fencing_token, p_result_kind, p_input_sha256, p_output_refs, p_receipt_sha256, p_release_id)
  returning id into v_result;
  update public.cs_work_items w
     set state = 'done', lease_token = null, lease_until = null, lease_owner = null, failure_code = null
   where w.id = v.id;
  if v.run_id is not null then
    v_seq := cs_private.cs_append_run_event(v.workspace_id, v.run_id, 'work.finished',
      jsonb_build_object('node_id', v.node_id, 'stage', v.stage, 'stage_result_id', v_result,
                         'fencing_token', v.fencing_token));
    -- Node output is recorded; downstream release and run completion belong to
    -- the CS-B3 DAG scheduler. The run is NOT marked completed here.
    update public.cs_run_nodes n set state = 'succeeded', output_refs = p_output_refs, last_event_sequence = v_seq
     where n.workspace_id = v.workspace_id and n.run_id = v.run_id and n.node_id = v.node_id;
  else
    perform cs_private.cs_append_operation_event(v.workspace_id, v.operation_id, 'work.finished',
      jsonb_build_object('step', v.operation_step_key, 'stage', v.stage, 'stage_result_id', v_result,
                         'fencing_token', v.fencing_token));
    if v_op.state <> 'failed' then
      update public.cs_operations o set state = 'completed', completed_at = now()
       where o.workspace_id = v.workspace_id and o.id = v.operation_id;
      perform cs_private.cs_append_operation_event(v.workspace_id, v.operation_id, 'operation.completed',
        jsonb_build_object('result_refs', v_op.result_refs));
    end if;
  end if;
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
  if v.run_id is not null then
    v_seq := cs_private.cs_append_run_event(v.workspace_id, v.run_id,
      case when v_next = 'dead' then 'work.dead' else 'work.retry_scheduled' end,
      jsonb_build_object('node_id', v.node_id, 'stage', v.stage, 'failure_code', p_failure_code,
                         'attempt_count', v.attempt_count));
    update public.cs_run_nodes n
       set state = case when v_next = 'dead' then 'failed'::public.cs_node_state else 'queued'::public.cs_node_state end,
           last_event_sequence = v_seq
     where n.workspace_id = v.workspace_id and n.run_id = v.run_id and n.node_id = v.node_id;
  else
    perform cs_private.cs_append_operation_event(v.workspace_id, v.operation_id,
      case when v_next = 'dead' then 'work.dead' else 'work.retry_scheduled' end,
      jsonb_build_object('step', v.operation_step_key, 'stage', v.stage, 'failure_code', p_failure_code,
                         'attempt_count', v.attempt_count));
    if v_next = 'dead' then
      update public.cs_operations o
         set state = 'failed',
             error = jsonb_build_object('code', p_failure_code, 'message', 'retry limit reached for step '
                                        || v.operation_step_key, 'retryable', false,
                                        'field_paths', '[]'::jsonb, 'related_ids', jsonb_build_array(v.id))
       where o.workspace_id = v.workspace_id and o.id = v.operation_id and o.state not in ('completed', 'failed', 'cancelled');
    end if;
  end if;
  return v_next;
end $$;

-- -----------------------------------------------------------------------------
-- 12. Worker: brand package import stage (lease-fenced, operation-owned)
-- -----------------------------------------------------------------------------
create or replace function cs_private.cs_lock_import_lease(p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint)
returns public.cs_work_items
language plpgsql set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
begin
  v := cs_private.cs_lock_live_lease(p_work_item_id, p_lease_token, p_fencing_token);
  if v.operation_id is null or v.stage <> 'import_brand_package' then
    raise exception 'wrong_stage' using errcode = 'CS409';
  end if;
  if not exists (select 1 from public.cs_operations o where o.workspace_id = v.workspace_id and o.id = v.operation_id
                   and o.kind = 'brand_package_import' and o.state = 'running') then
    raise exception 'operation_not_running' using errcode = 'CS409';
  end if;
  return v;
end $$;

create or replace function public.cs_import_observe_source(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint,
  p_source_kind text, p_version_id uuid, p_revision integer, p_status text, p_runtime_principal text)
returns void
language plpgsql set search_path = public, pg_temp as $$
declare v public.cs_work_items;
begin
  v := cs_private.cs_lock_import_lease(p_work_item_id, p_lease_token, p_fencing_token);
  perform cs_private.cs_upsert_source_head(v.workspace_id, (v.payload_ref ->> 'binding_id')::uuid, p_source_kind,
                                           p_version_id, p_revision, p_status, 'worker:' || p_runtime_principal);
end $$;

-- Records one mirrored brand file. Bytes were fetched from the source authority,
-- hashed by the worker and written at the exact key returned here; the mapping
-- (studio workspace, source authority/workspace/version/hash) reuses the mirror on replay.
create or replace function public.cs_import_commit_asset(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_source_ref jsonb,
  p_asset_id uuid, p_asset_version_id uuid, p_label text, p_mime_type text, p_size_bytes bigint, p_sha256 text,
  p_width integer, p_height integer, p_storage_key text, p_runtime_principal text)
returns table (asset_id uuid, asset_version_id uuid, storage_key text, reused boolean)
language plpgsql set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
  v_binding public.cs_external_bindings;
  v_existing public.cs_source_asset_mirrors;
  v_ext text := cs_private.cs_extension_for_mime(p_mime_type);
  v_key text;
begin
  v := cs_private.cs_lock_import_lease(p_work_item_id, p_lease_token, p_fencing_token);
  select * into v_binding from public.cs_external_bindings b
   where b.workspace_id = v.workspace_id and b.id = (v.payload_ref ->> 'binding_id')::uuid;
  if p_source_ref ->> 'authority' <> v_binding.authority::text
     or p_source_ref ->> 'workspace_id' <> v_binding.external_workspace_id then
    raise exception 'scope_mismatch: asset is not in the connected workspace' using errcode = 'CS403';
  end if;
  if p_sha256 is distinct from p_source_ref ->> 'sha256' then
    raise exception 'asset_hash_mismatch' using errcode = 'CS422';
  end if;
  if p_mime_type is distinct from p_source_ref ->> 'mime_type' or v_ext is null then
    raise exception 'unsupported_media_type' using errcode = 'CS422';
  end if;
  if p_runtime_principal is null or p_runtime_principal !~ '^[A-Za-z0-9_.:-]{1,100}$' then
    raise exception 'runtime_principal_required' using errcode = 'CS422';
  end if;
  select * into v_existing from public.cs_source_asset_mirrors m
   where m.workspace_id = v.workspace_id and m.source_authority = v_binding.authority
     and m.source_workspace_id = v_binding.external_workspace_id::uuid
     and m.source_asset_version_id = (p_source_ref ->> 'asset_version_id')::uuid and m.source_sha256 = p_sha256;
  if found then
    return query select v_existing.asset_id, v_existing.asset_version_id, av.storage_key, true
      from public.cs_asset_versions av where av.workspace_id = v.workspace_id and av.id = v_existing.asset_version_id;
    return;
  end if;
  v_key := format('%s/%s/%s/%s/%s.%s', v.environment_id, v.workspace_id, p_asset_id, p_asset_version_id, p_sha256, v_ext);
  if p_storage_key is distinct from v_key then
    raise exception 'storage_key_mismatch' using errcode = 'CS422';
  end if;
  insert into public.cs_assets (id, workspace_id, label, kind) values (p_asset_id, v.workspace_id, btrim(p_label), 'source');
  insert into public.cs_asset_versions (id, workspace_id, asset_id, revision, environment_id, storage_key, sha256,
    mime_type, size_bytes, width, height, source_refs, work_item_id, fencing_token, runtime_principal, rights)
  values (p_asset_version_id, v.workspace_id, p_asset_id, 1, v.environment_id, v_key, p_sha256, p_mime_type, p_size_bytes,
          p_width, p_height, jsonb_build_array(p_source_ref), v.id, v.fencing_token, p_runtime_principal, '{}'::jsonb);
  update public.cs_assets a set latest_version_id = p_asset_version_id
   where a.workspace_id = v.workspace_id and a.id = p_asset_id;
  insert into public.cs_source_asset_mirrors (workspace_id, source_authority, source_workspace_id, source_asset_id,
    source_asset_version_id, source_sha256, source_ref, asset_id, asset_version_id, operation_id)
  values (v.workspace_id, v_binding.authority, v_binding.external_workspace_id::uuid, (p_source_ref ->> 'asset_id')::uuid,
          (p_source_ref ->> 'asset_version_id')::uuid, p_sha256, p_source_ref, p_asset_id, p_asset_version_id, v.operation_id);
  return query select p_asset_id, p_asset_version_id, v_key, false;
end $$;

-- Attaches the exact approved package. Refuses unapproved/stale sources and any
-- package whose logo/font/reference refs are not exactly present in assets[] or
-- whose files were not mirrored with matching bytes in this workspace.
create or replace function public.cs_import_attach_package(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint,
  p_document jsonb, p_canonical text, p_source_ref jsonb, p_source_status text, p_approval_receipt_ref jsonb)
returns table (version_id uuid, sha256 text, reused boolean)
language plpgsql set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
  v_binding public.cs_external_bindings;
  v_head public.cs_source_heads;
  v_sha text;
  v_existing public.cs_source_imports;
  v_object uuid;
  v_rev integer;
  v_bad text;
  v_result jsonb;
  v_assets jsonb;
  v_rights jsonb;
  v_expiry timestamptz;
begin
  v := cs_private.cs_lock_import_lease(p_work_item_id, p_lease_token, p_fencing_token);
  select * into v_binding from public.cs_external_bindings b
   where b.workspace_id = v.workspace_id and b.id = (v.payload_ref ->> 'binding_id')::uuid;
  if v_binding.status <> 'active' then
    raise exception 'authorization_blocked: connection revoked' using errcode = 'CS403';
  end if;
  -- the admitted ref is the only package this operation may produce
  if not cs_private.cs_is_version_ref(p_source_ref)
     or p_source_ref ->> 'version_id' <> v.payload_ref #>> '{package_ref,version_id}'
     or p_source_ref ->> 'sha256' <> v.payload_ref #>> '{package_ref,sha256}'
     or p_source_ref ->> 'workspace_id' <> v_binding.external_workspace_id
     or p_source_ref ->> 'authority' <> v_binding.authority::text then
    raise exception 'source_revision_changed: exported source differs from the admitted package ref' using errcode = 'CS409';
  end if;
  if p_source_status <> 'approved' then
    raise exception 'brand_not_approved: source is %', p_source_status using errcode = 'CS409';
  end if;
  select * into v_head from public.cs_source_heads h
   where h.workspace_id = v.workspace_id and h.binding_id = v_binding.id and h.source_kind = p_source_ref ->> 'kind';
  if found and (v_head.current_version_id <> (p_source_ref ->> 'version_id')::uuid or v_head.current_status <> 'approved') then
    raise exception 'approval_stale: a newer or withdrawn upstream revision is current' using errcode = 'CS409';
  end if;
  v_sha := cs_private.cs_assert_document_digest(p_document, p_canonical);
  if p_document ->> 'schema_version' <> 'brand-package-v1' or p_document ->> 'authority' <> 'brand-ci-studio' then
    raise exception 'unsupported_schema_version' using errcode = 'CS422';
  end if;
  if p_document #>> '{source,approval_receipt_id}' <> p_document #>> '{approval,receipt_id}'
     or p_approval_receipt_ref ->> 'receipt_id' <> p_document #>> '{approval,receipt_id}' then
    raise exception 'approval_receipt_mismatch' using errcode = 'CS422';
  end if;
  -- every referenced brand file is declared in assets[] with the same identity and digest
  select string_agg(x.path, ',') into v_bad from (
    select 'logos' path, l -> 'asset' ref from jsonb_array_elements(p_document -> 'logos') l
    union all
    select 'typography', f from jsonb_array_elements(p_document -> 'typography') t, jsonb_array_elements(t -> 'font_assets') f
    union all
    select 'imagery', r from jsonb_array_elements(p_document #> '{imagery,reference_assets}') r
    union all
    select 'source.approved_pdf', p_document #> '{source,approved_pdf}') x
   where not exists (select 1 from jsonb_array_elements(p_document -> 'assets') a where a -> 'ref' = x.ref);
  if v_bad is not null then
    raise exception 'asset_unresolved: % reference files not declared in assets', v_bad using errcode = 'CS422';
  end if;
  -- every declared file is mirrored with matching bytes in this Studio workspace
  select string_agg(a #>> '{ref,asset_version_id}', ',') into v_bad
    from jsonb_array_elements(p_document -> 'assets') a
   where not exists (select 1 from public.cs_source_asset_mirrors m
                      where m.workspace_id = v.workspace_id and m.source_authority::text = a #>> '{ref,authority}'
                        and m.source_workspace_id::text = a #>> '{ref,workspace_id}'
                        and m.source_asset_version_id::text = a #>> '{ref,asset_version_id}'
                        and m.source_sha256 = a #>> '{ref,sha256}');
  if v_bad is not null then
    raise exception 'source_unavailable: files not mirrored: %', v_bad using errcode = 'CS409';
  end if;

  select * into v_existing from public.cs_source_imports s
   where s.workspace_id = v.workspace_id and s.binding_id = v_binding.id and s.source_kind = p_source_ref ->> 'kind'
     and s.source_version_id = (p_source_ref ->> 'version_id')::uuid;
  if found then
    if v_existing.sha256 <> v_sha then
      raise exception 'asset_hash_mismatch: same source version produced a different package' using errcode = 'CS409';
    end if;
  else
    v_object := (p_document ->> 'id')::uuid;
    v_rev := (p_document ->> 'revision')::int;
    if exists (select 1 from public.cs_source_imports s where s.workspace_id = v.workspace_id
                 and s.object_id = v_object and s.revision >= v_rev) then
      raise exception 'source_revision_regressed' using errcode = 'CS409';
    end if;
    select coalesce(jsonb_agg(jsonb_build_object('asset_version_id', a #>> '{ref,asset_version_id}',
                                                 'status', a #>> '{rights,status}',
                                                 'expires_at', a #> '{rights,expires_at}',
                                                 'receipt_id', a #>> '{rights,receipt_id}')), '[]'::jsonb),
           min((a #>> '{rights,expires_at}')::timestamptz)
      into v_rights, v_expiry
      from jsonb_array_elements(p_document -> 'assets') a;
    insert into public.cs_source_imports (workspace_id, object_id, version_id, revision, kind, authority, binding_id,
      source_ref, source_version_id, source_revision, source_kind, payload, sha256, approval_receipt_ref, rights,
      rights_expires_at, applicability_checked_at, operation_id)
    values (v.workspace_id, v_object, (p_document ->> 'version_id')::uuid, v_rev, 'brand_package', v_binding.authority,
            v_binding.id, p_source_ref, (p_source_ref ->> 'version_id')::uuid, (p_source_ref ->> 'revision')::int,
            p_source_ref ->> 'kind', p_document, v_sha, p_approval_receipt_ref, v_rights, v_expiry, now(), v.operation_id);
    select * into v_existing from public.cs_source_imports s
     where s.workspace_id = v.workspace_id and s.version_id = (p_document ->> 'version_id')::uuid;
  end if;
  perform cs_private.cs_upsert_source_head(v.workspace_id, v_binding.id, p_source_ref ->> 'kind',
    (p_source_ref ->> 'version_id')::uuid, (p_source_ref ->> 'revision')::int, 'approved', 'worker:import');

  v_result := jsonb_build_array(jsonb_build_object(
    'authority', v_existing.payload ->> 'authority', 'workspace_id', v_existing.payload ->> 'workspace_id',
    'kind', 'brand-package', 'id', v_existing.object_id, 'version_id', v_existing.version_id,
    'revision', v_existing.revision, 'sha256', v_existing.sha256));
  select coalesce(jsonb_agg(jsonb_build_object('authority', 'creative-studio', 'workspace_id', av.workspace_id,
                    'asset_id', av.asset_id, 'asset_version_id', av.id, 'revision', av.revision, 'sha256', av.sha256,
                    'mime_type', av.mime_type) order by av.id), '[]'::jsonb)
    into v_assets
    from jsonb_array_elements(v_existing.payload -> 'assets') a
    join public.cs_source_asset_mirrors m
      on m.workspace_id = v.workspace_id and m.source_asset_version_id::text = a #>> '{ref,asset_version_id}'
     and m.source_sha256 = a #>> '{ref,sha256}' and m.source_authority::text = a #>> '{ref,authority}'
    join public.cs_asset_versions av on av.workspace_id = m.workspace_id and av.id = m.asset_version_id;
  update public.cs_operations o set result_refs = v_result, result_asset_refs = v_assets
   where o.workspace_id = v.workspace_id and o.id = v.operation_id;
  perform cs_private.cs_append_operation_event(v.workspace_id, v.operation_id, 'import.attached',
    jsonb_build_object('package', v_result -> 0, 'asset_count', jsonb_array_length(v_assets)));
  return query select v_existing.version_id, v_existing.sha256, v_existing.operation_id <> v.operation_id;
end $$;

-- Typed, non-retryable stage refusal (brand_not_approved, asset_hash_mismatch, ...).
create or replace function public.cs_import_fail(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_code text, p_message text, p_field_paths jsonb)
returns void
language plpgsql set search_path = public, pg_temp as $$
declare v public.cs_work_items;
begin
  v := cs_private.cs_lock_import_lease(p_work_item_id, p_lease_token, p_fencing_token);
  if p_code is null or p_code !~ '^[a-z][a-z0-9_]{0,99}$' then
    raise exception 'invalid_failure_code' using errcode = 'CS422';
  end if;
  update public.cs_operations o
     set state = 'failed',
         error = jsonb_build_object('code', p_code, 'message', left(coalesce(nullif(p_message, ''), p_code), 2000),
                                    'retryable', false, 'field_paths', coalesce(p_field_paths, '[]'::jsonb),
                                    'related_ids', '[]'::jsonb)
   where o.workspace_id = v.workspace_id and o.id = v.operation_id;
  perform cs_private.cs_append_operation_event(v.workspace_id, v.operation_id, 'operation.failed',
    jsonb_build_object('code', p_code));
end $$;

-- -----------------------------------------------------------------------------
-- 13. Function privileges
-- -----------------------------------------------------------------------------
do $$
declare r record;
begin
  for r in select p.oid::regprocedure as sig
             from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where ((n.nspname = 'public' and p.proname like 'cs\_%') or n.nspname = 'cs_private')
              and p.proname <> 'cs_storage_allowed' loop
    execute format('revoke all on function %s from public, anon, authenticated', r.sig);
  end loop;
end $$;

grant execute on function cs_private.cs_assert_document_digest(jsonb, text), cs_private.cs_is_version_ref(jsonb),
  cs_private.cs_assert_audit(jsonb, uuid, uuid), cs_private.cs_require_produce(uuid),
  cs_private.cs_audit(uuid, uuid, text, text, jsonb), cs_private.cs_brand_eligibility(uuid, uuid, text, text),
  cs_private.cs_require_brand_eligible(uuid, uuid, text, text), cs_private.cs_resolve_local_ref(uuid, jsonb, text),
  cs_private.cs_upsert_source_head(uuid, uuid, text, uuid, integer, text, text),
  cs_private.cs_binding_guard(), cs_private.cs_operation_guard(), cs_private.cs_draft_guard()
  to cs_api;
grant execute on function public.cs_bind_external_workspace(uuid, public.cs_external_authority, text, jsonb, uuid, text),
  public.cs_record_source_head(uuid, uuid, text, uuid, integer, text),
  public.cs_admit_brand_import(uuid, uuid, jsonb, text, text, uuid, text),
  public.cs_brand_package_eligibility(uuid, uuid, text, text),
  public.cs_save_creative_request(uuid, jsonb, text, text, text, uuid, text),
  public.cs_save_treatment(uuid, jsonb, text, text, text, uuid, text),
  public.cs_accept_treatment(uuid, jsonb, text, text, text, uuid, text),
  public.cs_save_authoring_draft(uuid, jsonb, text, integer)
  to cs_api;
grant execute on function cs_private.cs_append_operation_event(uuid, uuid, text, jsonb),
  cs_private.cs_lock_import_lease(uuid, uuid, bigint), cs_private.cs_assert_document_digest(jsonb, text),
  cs_private.cs_is_version_ref(jsonb), cs_private.cs_upsert_source_head(uuid, uuid, text, uuid, integer, text, text),
  cs_private.cs_brand_eligibility(uuid, uuid, text, text), cs_private.cs_operation_guard(), cs_private.cs_binding_guard()
  to cs_worker;
grant execute on function public.cs_import_observe_source(uuid, uuid, bigint, text, uuid, integer, text, text),
  public.cs_import_commit_asset(uuid, uuid, bigint, jsonb, uuid, uuid, text, text, bigint, text, integer, integer, text, text),
  public.cs_import_attach_package(uuid, uuid, bigint, jsonb, text, jsonb, text, jsonb),
  public.cs_import_fail(uuid, uuid, bigint, text, text, jsonb)
  to cs_worker;
