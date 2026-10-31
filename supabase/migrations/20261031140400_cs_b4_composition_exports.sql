-- =============================================================================
-- CS-B4 / NS-10801 — immutable composition versions, export manifests and
-- MOS handoffs (database-contract.json: cs_composition_versions, cs_exports,
-- cs_handoffs). Builds on CS-B1 (20261030210000) helpers: cs_can, cs_actor_id,
-- cs_require_actor, cs_idempotency_replay, cs_lock_live_lease,
-- cs_reject_mutation. CS-B1 objects are not modified.
--
--   * cs_save_composition_version (cs_api, producer): optimistic revision chain
--     (expected sha256 = If-Match), exact AssetRefs resolved in the same
--     workspace, idempotent by (workspace, operation, key).
--   * cs_publish_export (cs_worker, live publish_export lease): binds a stored
--     asset version (hash/MIME/size/dimensions) and a saved composition to an
--     immutable cs-export-v2 manifest (cs-export-v1 still accepted for manifests
--     issued before the 16:9 amendment; CS-INT F4).
--   * cs_enqueue_handoff (cs_api, producer): queues an immutable
--     cs-mos-handoff-v1 for exact, existing exports; never writes MOS tables.
-- JSON Schema + RFC 8785 hashing are verified by the service before the call
-- (service/composition); SQL enforces identity, scope, revision, references.
--
-- Access model: the four tables have RLS enabled+forced, NO policies and NO
-- table grants to any runtime role, so direct reads/writes by anon,
-- authenticated, cs_api and cs_worker are all refused. All access is through
-- the SECURITY DEFINER procedures below (search_path pinned, PUBLIC/anon/
-- authenticated revoked, actor only from verified request claims via
-- cs_private.cs_actor_id(), current membership checked with cs_can on every
-- call, every lookup filtered by workspace_id). This deliberately has no RLS
-- policies because CS-B1's migration, when re-run, drops every policy on
-- public.cs_% tables (see report).
-- Re-runnable: IF NOT EXISTS / OR REPLACE throughout.
-- =============================================================================

do $$
begin
  if to_regprocedure('cs_private.cs_can(uuid,text)') is null
     or to_regprocedure('cs_private.cs_lock_live_lease(uuid,uuid,bigint)') is null
     or to_regclass('public.cs_asset_versions') is null then
    raise exception 'cs_b4: CS-B1 platform foundation (20261030210000) is required';
  end if;
end $$;

-- -----------------------------------------------------------------------------
-- 1. Tables
-- -----------------------------------------------------------------------------
create table if not exists public.cs_composition_versions (
  workspace_id      uuid not null references public.cs_workspaces(id),
  object_id         uuid not null,
  version_id        uuid not null,
  revision          integer not null check (revision > 0),
  kind              text not null check (kind in ('image', 'video')),
  payload           jsonb not null check (jsonb_typeof(payload) = 'object'),
  sha256            text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  semantic_sha256   text not null check (semantic_sha256 ~ '^[0-9a-f]{64}$'),
  input_asset_refs  jsonb not null check (jsonb_typeof(input_asset_refs) = 'array'),
  renderer_revision text,
  created_by        uuid not null references auth.users(id),
  created_at        timestamptz not null default now(),
  primary key (workspace_id, version_id),
  unique (version_id),
  unique (workspace_id, object_id, revision),
  check (payload ->> 'schema_version' = 'cs-composition-v1'
     and payload ->> 'workspace_id' = workspace_id::text
     and payload ->> 'id' = object_id::text
     and payload ->> 'version_id' = version_id::text
     and payload ->> 'revision' = revision::text
     and payload ->> 'sha256' = sha256
     and payload ->> 'kind' = kind
     and payload ->> 'created_by' = created_by::text)
);

create table if not exists public.cs_exports (
  workspace_id           uuid not null references public.cs_workspaces(id),
  object_id              uuid not null,
  version_id             uuid not null,
  revision               integer not null check (revision > 0),
  composition_version_id uuid not null,
  asset_version_id       uuid not null,
  plan_version_id        uuid,     -- scoped FK to cs_compile_plans arrives with plans (CS-B2/B3)
  run_id                 uuid,
  manifest               jsonb not null check (jsonb_typeof(manifest) = 'object'),
  sha256                 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  file_sha256            text not null check (file_sha256 ~ '^[0-9a-f]{64}$'),
  work_item_id           uuid not null,
  fencing_token          bigint not null check (fencing_token > 0),
  runtime_principal      text not null check (length(runtime_principal) > 0),
  created_at             timestamptz not null default now(),
  primary key (workspace_id, version_id),
  unique (version_id),
  unique (workspace_id, object_id, revision),
  foreign key (workspace_id, composition_version_id)
    references public.cs_composition_versions(workspace_id, version_id),
  foreign key (workspace_id, asset_version_id) references public.cs_asset_versions(workspace_id, id),
  foreign key (workspace_id, run_id) references public.cs_runs(workspace_id, id),
  foreign key (workspace_id, work_item_id) references public.cs_work_items(workspace_id, id),
  check (manifest ->> 'schema_version' in ('cs-export-v1', 'cs-export-v2')
     and manifest ->> 'status' = 'complete'
     and manifest ->> 'requires_downstream_review' = 'true'
     and manifest ->> 'workspace_id' = workspace_id::text
     and manifest ->> 'id' = object_id::text
     and manifest ->> 'version_id' = version_id::text
     and manifest ->> 'revision' = revision::text
     and manifest ->> 'sha256' = sha256
     and manifest -> 'file' -> 'ref' ->> 'asset_version_id' = asset_version_id::text
     and manifest -> 'file' -> 'ref' ->> 'sha256' = file_sha256
     and manifest -> 'lineage' -> 'composition' ->> 'version_id' = composition_version_id::text)
);

create table if not exists public.cs_handoffs (
  id                    uuid primary key,
  workspace_id          uuid not null references public.cs_workspaces(id),
  version_id            uuid not null unique,
  destination_authority text not null check (destination_authority = 'marketing-os'),
  external_workspace_id uuid not null,
  idempotency_key       uuid not null,
  request_sha256        text not null check (request_sha256 ~ '^[0-9a-f]{64}$'),
  payload               jsonb not null check (jsonb_typeof(payload) = 'object'),
  sha256                text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  state                 text not null default 'queued'
                          check (state in ('queued', 'delivering', 'received', 'needs_reconciliation', 'failed')),
  receipt_ref           jsonb,
  created_by            uuid not null references auth.users(id),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  unique (workspace_id, id),
  unique (workspace_id, destination_authority, idempotency_key),
  check (payload ->> 'schema_version' = 'cs-mos-handoff-v1'
     and payload ->> 'requires_mos_review' = 'true'
     and payload ->> 'id' = id::text
     and payload ->> 'version_id' = version_id::text
     and payload ->> 'workspace_id' = workspace_id::text
     and payload ->> 'idempotency_key' = idempotency_key::text
     and payload ->> 'sha256' = sha256
     and payload ->> 'created_by' = created_by::text
     and payload -> 'target' ->> 'authority' = destination_authority
     and payload -> 'target' ->> 'workspace_id' = external_workspace_id::text),
  -- a receipt exists only once MOS has received the exact set
  check ((state = 'received') = (receipt_ref is not null))
);

create table if not exists public.cs_handoff_exports (
  workspace_id      uuid not null,
  handoff_id        uuid not null,
  export_version_id uuid not null,
  export_sha256     text not null check (export_sha256 ~ '^[0-9a-f]{64}$'),
  ordinal           integer not null check (ordinal >= 0),
  primary key (workspace_id, handoff_id, export_version_id),
  unique (workspace_id, handoff_id, ordinal),
  foreign key (workspace_id, handoff_id) references public.cs_handoffs(workspace_id, id),
  foreign key (workspace_id, export_version_id) references public.cs_exports(workspace_id, version_id)
);

do $$
declare t text;
begin
  foreach t in array array['cs_composition_versions', 'cs_exports', 'cs_handoff_exports'] loop
    execute format('drop trigger if exists %1$s_immutable on public.%1$s', t);
    execute format('create trigger %1$s_immutable before update or delete on public.%1$s '
                   'for each row execute function cs_private.cs_reject_mutation()', t);
  end loop;
end $$;

-- Handoff payload/identity are immutable; only delivery state may change (CS-B3/B5 deliver_handoff).
create or replace function cs_private.cs_b4_handoff_fixed() returns trigger
language plpgsql set search_path = public, pg_temp as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'cs_immutable_record: cs_handoffs rows cannot be deleted' using errcode = 'CS409';
  end if;
  if (new.id, new.workspace_id, new.version_id, new.destination_authority, new.external_workspace_id,
      new.idempotency_key, new.request_sha256, new.payload, new.sha256, new.created_by, new.created_at)
     is distinct from
     (old.id, old.workspace_id, old.version_id, old.destination_authority, old.external_workspace_id,
      old.idempotency_key, old.request_sha256, old.payload, old.sha256, old.created_by, old.created_at) then
    raise exception 'cs_immutable_record: handoff payload and identity are fixed' using errcode = 'CS409';
  end if;
  new.updated_at := now();
  return new;
end $$;
drop trigger if exists cs_handoffs_fixed on public.cs_handoffs;
create trigger cs_handoffs_fixed before update or delete on public.cs_handoffs
  for each row execute function cs_private.cs_b4_handoff_fixed();

-- -----------------------------------------------------------------------------
-- 2. RLS enabled + forced, no policies, no table grants (procedure-only access)
-- -----------------------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array['public.cs_composition_versions', 'public.cs_exports', 'public.cs_handoffs',
                           'public.cs_handoff_exports'] loop
    execute format('alter table %s enable row level security', t);
    execute format('alter table %s force row level security', t);
    execute format('revoke all on table %s from public, anon, authenticated', t);
  end loop;
end $$;

-- Drop policies an earlier draft of this file may have created (none are used).
do $$
declare r record;
begin
  for r in select schemaname, tablename, policyname from pg_policies
            where schemaname = 'public' and policyname like 'cs\_b4\_%' loop
    execute format('drop policy %I on %I.%I', r.policyname, r.schemaname, r.tablename);
  end loop;
end $$;
revoke all on public.cs_composition_versions, public.cs_exports, public.cs_handoffs, public.cs_handoff_exports
  from cs_api, cs_worker;

-- -----------------------------------------------------------------------------
-- 3. Procedures (SECURITY DEFINER, workspace-scoped; see access model above)
-- -----------------------------------------------------------------------------
-- Every JSON AssetRef must name an existing asset version of this workspace with
-- the same asset id, revision, digest and MIME type.
create or replace function cs_private.cs_b4_assert_asset_refs(p_workspace_id uuid, p_refs jsonb)
returns void
language plpgsql security definer set search_path = public, pg_temp as $$
declare r jsonb;
begin
  if p_refs is null or jsonb_typeof(p_refs) <> 'array' then
    raise exception 'invalid_asset_refs' using errcode = 'CS422';
  end if;
  for r in select * from jsonb_array_elements(p_refs) loop
    if coalesce(r ->> 'authority', '') <> 'creative-studio'
       or coalesce(r ->> 'workspace_id', '') <> p_workspace_id::text then
      raise exception 'scope_mismatch' using errcode = 'CS403';
    end if;
    if not exists (
      select 1 from public.cs_asset_versions v
       where v.workspace_id = p_workspace_id
         and v.id::text = (r ->> 'asset_version_id')
         and v.asset_id::text = (r ->> 'asset_id')
         and v.revision::text = (r ->> 'revision')
         and v.sha256 = (r ->> 'sha256')
         and v.mime_type = (r ->> 'mime_type')) then
      raise exception 'source_unavailable' using errcode = 'CS422';
    end if;
  end loop;
end $$;

create or replace function cs_private.cs_b4_require(p_workspace_id uuid, p_action text) returns uuid
language plpgsql stable security definer set search_path = public, pg_temp as $$
declare v_actor uuid := cs_private.cs_require_actor();
begin
  if not cs_private.cs_can(p_workspace_id, p_action) then
    if cs_private.cs_can(p_workspace_id, 'read') then
      raise exception 'capability_denied' using errcode = 'CS403';
    end if;
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  return v_actor;
end $$;

create or replace function public.cs_save_composition_version(
  p_workspace_id uuid, p_payload jsonb, p_semantic_sha256 text, p_input_asset_refs jsonb,
  p_expected_sha256 text, p_idempotency_key uuid, p_request_sha256 text)
returns table (version_id uuid, revision integer, sha256 text, replayed boolean)
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_b4_require(p_workspace_id, 'produce');
  v_prior jsonb;
  v_object uuid;
  v_rev integer;
  v_current record;
  v_account uuid;
begin
  v_prior := cs_private.cs_idempotency_replay(p_workspace_id, 'composition.save', p_idempotency_key, p_request_sha256);
  if v_prior is not null then
    return query select (v_prior ->> 'version_id')::uuid, (v_prior ->> 'revision')::integer,
                        v_prior ->> 'sha256', true;
    return;
  end if;
  if p_payload ->> 'workspace_id' is distinct from p_workspace_id::text then
    raise exception 'scope_mismatch' using errcode = 'CS403';
  end if;
  if p_payload ->> 'created_by' is distinct from v_actor::text then
    raise exception 'actor_mismatch' using errcode = 'CS422';
  end if;
  v_object := (p_payload ->> 'id')::uuid;
  v_rev := (p_payload ->> 'revision')::integer;
  -- serialize writers of one composition so the revision check is exact
  perform pg_advisory_xact_lock(hashtextextended('cs_composition:' || v_object::text, 0));
  select c.revision, c.sha256 into v_current
    from public.cs_composition_versions c
   where c.workspace_id = p_workspace_id and c.object_id = v_object
   order by c.revision desc limit 1;
  if not found then
    if v_rev <> 1 or p_expected_sha256 is not null then
      raise exception 'revision_conflict' using errcode = 'CS409';
    end if;
  elsif p_expected_sha256 is distinct from v_current.sha256 or v_rev <> v_current.revision + 1 then
    raise exception 'revision_conflict' using errcode = 'CS409';
  end if;
  perform cs_private.cs_b4_assert_asset_refs(p_workspace_id, p_input_asset_refs);
  insert into public.cs_composition_versions (workspace_id, object_id, version_id, revision, kind, payload, sha256,
                                              semantic_sha256, input_asset_refs, created_by)
  values (p_workspace_id, v_object, (p_payload ->> 'version_id')::uuid, v_rev, p_payload ->> 'kind', p_payload,
          p_payload ->> 'sha256', p_semantic_sha256, p_input_asset_refs, v_actor);
  insert into public.cs_idempotency (workspace_id, actor_id, operation, key, request_sha256, result_ref)
  values (p_workspace_id, v_actor, 'composition.save', p_idempotency_key, p_request_sha256,
          jsonb_build_object('version_id', p_payload ->> 'version_id', 'revision', v_rev, 'sha256', p_payload ->> 'sha256'));
  select w.account_id into v_account from public.cs_workspaces w where w.id = p_workspace_id;
  insert into public.cs_audit_events (workspace_id, account_id, actor_id, action, input_sha256, affected_refs)
  values (p_workspace_id, v_account, v_actor, 'composition.save', p_request_sha256,
          jsonb_build_array(jsonb_build_object('kind', 'composition', 'id', v_object,
                                               'version_id', p_payload ->> 'version_id')));
  return query select (p_payload ->> 'version_id')::uuid, v_rev, p_payload ->> 'sha256', false;
end $$;

create or replace function public.cs_publish_export(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_manifest jsonb, p_runtime_principal text)
returns table (export_version_id uuid, replayed boolean)
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
  v_file jsonb := p_manifest -> 'file';
  v_comp jsonb := p_manifest -> 'lineage' -> 'composition';
  v_existing record;
  v_account uuid;
begin
  v := cs_private.cs_lock_live_lease(p_work_item_id, p_lease_token, p_fencing_token);
  if v.stage <> 'publish_export' then
    raise exception 'wrong_stage' using errcode = 'CS409';
  end if;
  if p_runtime_principal is null or length(p_runtime_principal) = 0 then
    raise exception 'runtime_principal_required' using errcode = 'CS422';
  end if;
  if p_manifest ->> 'workspace_id' is distinct from v.workspace_id::text then
    raise exception 'scope_mismatch' using errcode = 'CS403';
  end if;
  select e.sha256 into v_existing from public.cs_exports e
   where e.workspace_id = v.workspace_id and e.version_id::text = p_manifest ->> 'version_id';
  if found then
    if v_existing.sha256 <> p_manifest ->> 'sha256' then
      raise exception 'export_version_conflict' using errcode = 'CS409';
    end if;
    return query select (p_manifest ->> 'version_id')::uuid, true;
    return;
  end if;
  if not exists (select 1 from public.cs_composition_versions c
                  where c.workspace_id = v.workspace_id and c.version_id::text = v_comp ->> 'version_id'
                    and c.sha256 = v_comp ->> 'sha256' and c.object_id::text = v_comp ->> 'id') then
    raise exception 'source_unavailable' using errcode = 'CS422';
  end if;
  -- the manifest's file must be exactly a stored version: hash, MIME, size, dimensions
  if v_file -> 'ref' ->> 'workspace_id' is distinct from v.workspace_id::text
     or v_file -> 'ref' ->> 'authority' is distinct from 'creative-studio'
     or not exists (
       select 1 from public.cs_asset_versions av
        where av.workspace_id = v.workspace_id
          and av.id::text = v_file -> 'ref' ->> 'asset_version_id'
          and av.asset_id::text = v_file -> 'ref' ->> 'asset_id'
          and av.revision::text = v_file -> 'ref' ->> 'revision'
          and av.sha256 = v_file -> 'ref' ->> 'sha256'
          and av.mime_type = v_file -> 'ref' ->> 'mime_type'
          and av.size_bytes::text = v_file ->> 'size_bytes'
          and av.width is not distinct from (v_file ->> 'width_px')::integer
          and av.height is not distinct from (v_file ->> 'height_px')::integer) then
    raise exception 'asset_hash_mismatch' using errcode = 'CS422';
  end if;
  insert into public.cs_exports (workspace_id, object_id, version_id, revision, composition_version_id,
                                 asset_version_id, plan_version_id, run_id, manifest, sha256, file_sha256,
                                 work_item_id, fencing_token, runtime_principal)
  values (v.workspace_id, (p_manifest ->> 'id')::uuid, (p_manifest ->> 'version_id')::uuid,
          (p_manifest ->> 'revision')::integer, (v_comp ->> 'version_id')::uuid,
          (v_file -> 'ref' ->> 'asset_version_id')::uuid,
          (p_manifest -> 'lineage' -> 'plan' ->> 'version_id')::uuid, v.run_id, p_manifest,
          p_manifest ->> 'sha256', v_file -> 'ref' ->> 'sha256', v.id, v.fencing_token, p_runtime_principal);
  select w.account_id into v_account from public.cs_workspaces w where w.id = v.workspace_id;
  insert into public.cs_audit_events (workspace_id, account_id, runtime_principal, action, input_sha256, affected_refs)
  values (v.workspace_id, v_account, p_runtime_principal, 'export.publish', p_manifest ->> 'sha256',
          jsonb_build_array(jsonb_build_object('kind', 'export', 'version_id', p_manifest ->> 'version_id')));
  return query select (p_manifest ->> 'version_id')::uuid, false;
end $$;

create or replace function public.cs_enqueue_handoff(
  p_workspace_id uuid, p_payload jsonb, p_idempotency_key uuid, p_request_sha256 text)
returns table (handoff_id uuid, state text, replayed boolean)
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_b4_require(p_workspace_id, 'produce');
  v_prior jsonb;
  v_id uuid := (p_payload ->> 'id')::uuid;
  r jsonb;
  i integer := 0;
  v_account uuid;
  v_bound text;
begin
  v_prior := cs_private.cs_idempotency_replay(p_workspace_id, 'handoff.marketing_os', p_idempotency_key,
                                              p_request_sha256);
  if v_prior is not null then
    return query select (v_prior ->> 'handoff_id')::uuid, h.state, true
                   from public.cs_handoffs h
                  where h.workspace_id = p_workspace_id and h.id = (v_prior ->> 'handoff_id')::uuid;
    return;
  end if;
  if p_payload ->> 'workspace_id' is distinct from p_workspace_id::text then
    raise exception 'scope_mismatch' using errcode = 'CS403';
  end if;
  if p_payload ->> 'created_by' is distinct from v_actor::text
     or p_payload ->> 'idempotency_key' is distinct from p_idempotency_key::text then
    raise exception 'invalid_mos_handoff' using errcode = 'CS422';
  end if;
  -- CS-INT F5: the MOS target must be this workspace's active, verified Marketing OS
  -- connection (CS-B2 cs_external_bindings). A MOS workspace named only in the request
  -- body is never sufficient (contract.md §2.2).
  select lower(b.external_workspace_id) into v_bound
    from public.cs_external_bindings b
   where b.workspace_id = p_workspace_id and b.authority = 'marketing-os' and b.status = 'active';
  if v_bound is null then
    raise exception 'mos_connection_required' using errcode = 'CS409';
  end if;
  if v_bound is distinct from lower(p_payload -> 'target' ->> 'workspace_id') then
    raise exception 'scope_mismatch' using errcode = 'CS403';
  end if;
  insert into public.cs_handoffs (id, workspace_id, version_id, destination_authority, external_workspace_id,
                                  idempotency_key, request_sha256, payload, sha256, created_by)
  values (v_id, p_workspace_id, (p_payload ->> 'version_id')::uuid, 'marketing-os',
          (p_payload -> 'target' ->> 'workspace_id')::uuid, p_idempotency_key, p_request_sha256, p_payload,
          p_payload ->> 'sha256', v_actor);
  for r in select * from jsonb_array_elements(p_payload -> 'exports') loop
    if r ->> 'authority' is distinct from 'creative-studio' or r ->> 'workspace_id' is distinct from p_workspace_id::text
       or not exists (select 1 from public.cs_exports e
                       where e.workspace_id = p_workspace_id and e.version_id::text = r ->> 'version_id'
                         and e.object_id::text = r ->> 'id' and e.sha256 = r ->> 'sha256') then
      raise exception 'export_unavailable' using errcode = 'CS422';
    end if;
    insert into public.cs_handoff_exports (workspace_id, handoff_id, export_version_id, export_sha256, ordinal)
    values (p_workspace_id, v_id, (r ->> 'version_id')::uuid, r ->> 'sha256', i);
    i := i + 1;
  end loop;
  if i = 0 then
    raise exception 'invalid_mos_handoff' using errcode = 'CS422';
  end if;
  insert into public.cs_idempotency (workspace_id, actor_id, operation, key, request_sha256, result_ref)
  values (p_workspace_id, v_actor, 'handoff.marketing_os', p_idempotency_key, p_request_sha256,
          jsonb_build_object('handoff_id', v_id));
  select w.account_id into v_account from public.cs_workspaces w where w.id = p_workspace_id;
  insert into public.cs_audit_events (workspace_id, account_id, actor_id, action, input_sha256, affected_refs)
  values (p_workspace_id, v_account, v_actor, 'handoff.enqueue', p_request_sha256,
          jsonb_build_array(jsonb_build_object('kind', 'handoff', 'id', v_id)));
  return query select v_id, 'queued'::text, false;
end $$;


-- Reads: current read capability, rows filtered by workspace; absent or hidden => CS404.
create or replace function public.cs_get_composition_version(p_workspace_id uuid, p_object_id uuid, p_version_id uuid)
returns jsonb
language plpgsql stable security definer set search_path = public, pg_temp as $$
declare v jsonb;
begin
  perform cs_private.cs_b4_require(p_workspace_id, 'read');
  if p_object_id is null and p_version_id is null then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  -- version_id null => latest revision of the object
  select c.payload into v from public.cs_composition_versions c
   where c.workspace_id = p_workspace_id
     and (p_object_id is null or c.object_id = p_object_id)
     and (p_version_id is null or c.version_id = p_version_id)
   order by c.revision desc limit 1;
  if v is null then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  return v;
end $$;

create or replace function public.cs_get_export(p_workspace_id uuid, p_version_id uuid, p_sha256 text)
returns jsonb
language plpgsql stable security definer set search_path = public, pg_temp as $$
declare v jsonb;
begin
  perform cs_private.cs_b4_require(p_workspace_id, 'read');
  select e.manifest into v from public.cs_exports e
   where e.workspace_id = p_workspace_id and e.version_id = p_version_id
     and (p_sha256 is null or e.sha256 = p_sha256);
  if v is null then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  return v;
end $$;

create or replace function public.cs_get_handoff(p_workspace_id uuid, p_handoff_id uuid)
returns table (payload jsonb, state text, receipt_ref jsonb)
language plpgsql stable security definer set search_path = public, pg_temp as $$
begin
  perform cs_private.cs_b4_require(p_workspace_id, 'read');
  return query select h.payload, h.state, h.receipt_ref from public.cs_handoffs h
                where h.workspace_id = p_workspace_id and h.id = p_handoff_id;
  if not found then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
end $$;

-- -----------------------------------------------------------------------------
-- 4. Function privileges
-- -----------------------------------------------------------------------------
do $$
declare r record;
begin
  for r in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where (n.nspname = 'public' and p.proname in ('cs_save_composition_version', 'cs_publish_export',
                     'cs_enqueue_handoff', 'cs_get_composition_version', 'cs_get_export', 'cs_get_handoff'))
               or (n.nspname = 'cs_private' and p.proname like 'cs\_b4\_%') loop
    execute format('revoke all on function %s from public, anon, authenticated, cs_api, cs_worker', r.sig);
  end loop;
end $$;

grant execute on function public.cs_save_composition_version(uuid, jsonb, text, jsonb, text, uuid, text),
  public.cs_enqueue_handoff(uuid, jsonb, uuid, text),
  public.cs_get_composition_version(uuid, uuid, uuid),
  public.cs_get_export(uuid, uuid, text),
  public.cs_get_handoff(uuid, uuid) to cs_api;
grant execute on function public.cs_publish_export(uuid, uuid, bigint, jsonb, text) to cs_worker;
