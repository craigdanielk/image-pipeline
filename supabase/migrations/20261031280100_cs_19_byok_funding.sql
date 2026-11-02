-- CS-19 (BETA-WAVE S2, Creative Studio side): BYOK Higgsfield + media funding policy.
--
-- Policy per workspace (default byok_only = fail closed):
--   byok_only       the platform provider credential is NEVER usable for this workspace; only the workspace's own
--                   sealed credential (stored once over the signed gateway) is resolved. Refusal: byok_required.
--   platform_funded the platform credential may be used, still bounded by the owner-approved budget policy ceiling.
-- Enforced here in SQL (triggers on run admission and on attempt creation/dispatch + the worker credential read),
-- not only in the API. Every credential read by the worker is recorded in cs_private.cs_credential_uses (who paid).
-- Idempotent. Slot: 20261031280100 (CS range 28xxxx; MOS S2 uses 27xxxx).

create table if not exists cs_private.cs_workspace_funding (
  workspace_id  uuid primary key references public.cs_workspaces(id),
  media_funding text not null default 'byok_only' check (media_funding in ('byok_only', 'platform_funded')),
  set_by        uuid references auth.users(id),
  updated_at    timestamptz not null default clock_timestamp()
);

create table if not exists cs_private.cs_credential_uses (
  id                    uuid primary key default gen_random_uuid(),
  workspace_id          uuid not null references public.cs_workspaces(id),
  attempt_id            uuid not null,
  credential_version_id uuid not null references cs_private.cs_credentials(version_id),
  credential_mode       public.cs_credential_mode not null,
  used_at               timestamptz not null default clock_timestamp()
);
create index if not exists cs_credential_uses_ws_idx on cs_private.cs_credential_uses(workspace_id, used_at desc);

do $$
declare t text;
begin
  foreach t in array array['cs_workspace_funding', 'cs_credential_uses'] loop
    execute format('alter table cs_private.%s enable row level security', t);
    execute format('alter table cs_private.%s force row level security', t);
    execute format('revoke all on table cs_private.%s from public, anon, authenticated, cs_api, cs_worker', t);
  end loop;
end $$;
drop trigger if exists cs_credential_uses_immutable on cs_private.cs_credential_uses;
create trigger cs_credential_uses_immutable before update or delete on cs_private.cs_credential_uses
  for each row execute function cs_private.cs_reject_mutation();

create or replace function cs_private.cs_workspace_media_funding(p_workspace_id uuid) returns text
language sql stable security definer set search_path = public, pg_temp as $$
  select coalesce((select f.media_funding from cs_private.cs_workspace_funding f where f.workspace_id = p_workspace_id),
                  'byok_only');
$$;

-- Guard: admission insert, attempt insert, attempt entering 'dispatching'.
create or replace function cs_private.cs_19_funding_guard() returns trigger
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_mode public.cs_credential_mode; v_owner uuid; v_policy_platform boolean := false;
begin
  if tg_table_name = 'cs_attempts' and tg_op = 'UPDATE' then
    if not ((to_jsonb(new) ->> 'state') = 'dispatching' and (to_jsonb(old) ->> 'state') is distinct from 'dispatching') then
      return new;
    end if;
  end if;
  if tg_table_name = 'cs_run_admissions' then
    v_policy_platform := (to_jsonb(new) ->> 'funding_source') = 'platform';
  end if;
  select c.mode, c.workspace_id into v_mode, v_owner from cs_private.cs_credentials c
   where c.version_id = new.credential_version_id;
  if v_mode = 'workspace' and v_owner is distinct from new.workspace_id then
    raise exception 'credential_scope_mismatch: credential belongs to another workspace' using errcode = 'CS403';
  end if;
  if (v_mode = 'platform' or v_policy_platform)
     and cs_private.cs_workspace_media_funding(new.workspace_id) <> 'platform_funded' then
    raise exception 'byok_required: add your Higgsfield key in Integrations; the platform credential is not available to this workspace'
      using errcode = 'CS403';
  end if;
  return new;
end $$;
revoke all on function cs_private.cs_19_funding_guard() from public, anon, authenticated, cs_api, cs_worker;

drop trigger if exists cs_run_admissions_funding_guard on public.cs_run_admissions;
create trigger cs_run_admissions_funding_guard before insert on public.cs_run_admissions
  for each row execute function cs_private.cs_19_funding_guard();
drop trigger if exists cs_attempts_funding_guard on public.cs_attempts;
create trigger cs_attempts_funding_guard before insert or update of state on public.cs_attempts
  for each row execute function cs_private.cs_19_funding_guard();

-- Worker credential read: same rule, and an append-only use record (the proof "platform key unused").
create or replace function public.cs_attempt_credential(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_attempt_id uuid)
returns table (credential_id uuid, version_id uuid, mode public.cs_credential_mode, provider_account_fingerprint text,
               ciphertext bytea, nonce bytea, encryption_key_version text)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare v public.cs_work_items; c cs_private.cs_credentials;
begin
  v := cs_private.cs_b3_lease(p_work_item_id, p_lease_token, p_fencing_token, 'provider_submit');
  select x.* into c
    from public.cs_attempts a join cs_private.cs_credentials x on x.version_id = a.credential_version_id
   where a.workspace_id = v.workspace_id and a.id = p_attempt_id and a.run_id = v.run_id and a.node_id = v.node_id
     and x.status = 'active';
  if c.version_id is null then
    return;
  end if;
  if c.mode = 'platform' and cs_private.cs_workspace_media_funding(v.workspace_id) <> 'platform_funded' then
    raise exception 'byok_required: add your Higgsfield key in Integrations; the platform credential is not available to this workspace'
      using errcode = 'CS403';
  end if;
  insert into cs_private.cs_credential_uses (workspace_id, attempt_id, credential_version_id, credential_mode)
  values (v.workspace_id, p_attempt_id, c.version_id, c.mode);
  return query select c.id, c.version_id, c.mode, c.provider_account_fingerprint, c.ciphertext, c.nonce,
                      c.encryption_key_version;
end $$;
revoke all on function public.cs_attempt_credential(uuid, uuid, bigint, uuid) from public, anon, authenticated, cs_api, cs_worker;
grant execute on function public.cs_attempt_credential(uuid, uuid, bigint, uuid) to cs_worker;

-- MOS-authorised funding mode. Two routes call this (byok-only vs platform), signed with distinct assertion actions.
create or replace function public.cs_set_workspace_funding(p_workspace_id uuid, p_media_funding text)
returns text language plpgsql security definer set search_path = public, pg_temp as $$
declare v_actor uuid := cs_private.cs_require_actor();
begin
  if p_media_funding not in ('byok_only', 'platform_funded') then
    perform cs_private.cs_b3_refuse('invalid_funding', 'media_funding must be byok_only or platform_funded', 'CS422');
  end if;
  if not cs_private.cs_can(p_workspace_id, 'read') then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  if not cs_private.cs_can(p_workspace_id, 'manage') then
    perform cs_private.cs_b3_refuse('capability_denied', null, 'CS403');
  end if;
  insert into cs_private.cs_workspace_funding (workspace_id, media_funding, set_by)
  values (p_workspace_id, p_media_funding, v_actor)
  on conflict (workspace_id) do update set media_funding = excluded.media_funding, set_by = excluded.set_by,
                                           updated_at = clock_timestamp();
  insert into public.cs_audit_events (account_id, workspace_id, actor_id, action, affected_refs)
  select w.account_id, p_workspace_id, v_actor, 'funding.set',
         jsonb_build_array(jsonb_build_object('kind', 'media_funding', 'value', p_media_funding))
    from public.cs_workspaces w where w.id = p_workspace_id;
  return p_media_funding;
end $$;

-- Readable state: never any key material, only presence + a short fingerprint tail.
create or replace function public.cs_workspace_funding_state(p_workspace_id uuid, p_provider text)
returns table (media_funding text, workspace_credential boolean, fingerprint_tail text, credential_since timestamptz,
               platform_credential_usable boolean)
language plpgsql stable security definer set search_path = public, pg_temp as $$
begin
  perform cs_private.cs_require_actor();
  if not cs_private.cs_can(p_workspace_id, 'read') then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  if not cs_private.cs_can(p_workspace_id, 'manage') then   -- reviewers never see credential/funding state
    perform cs_private.cs_b3_refuse('capability_denied', null, 'CS403');
  end if;
  return query
    select cs_private.cs_workspace_media_funding(p_workspace_id),
           c.version_id is not null,
           right(c.provider_account_fingerprint, 4),
           c.created_at,
           cs_private.cs_workspace_media_funding(p_workspace_id) = 'platform_funded'
      from (select 1) one
      left join cs_private.cs_credentials c
        on c.workspace_id = p_workspace_id and c.provider = p_provider and c.status = 'active' and c.mode = 'workspace';
end $$;

-- Who paid, for the cost export (E10): workspace | platform.
create or replace function public.cs_run_payer(p_workspace_id uuid, p_run_id uuid)
returns text language plpgsql stable security definer set search_path = public, pg_temp as $$
declare v text;
begin
  perform cs_private.cs_require_actor();
  if not cs_private.cs_can(p_workspace_id, 'read') then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  select a.funding_source::text into v from public.cs_run_admissions a
   where a.workspace_id = p_workspace_id and a.run_id = p_run_id;
  return v;
end $$;

revoke all on function public.cs_set_workspace_funding(uuid, text), public.cs_workspace_funding_state(uuid, text),
  public.cs_run_payer(uuid, uuid), cs_private.cs_workspace_media_funding(uuid)
  from public, anon, authenticated, cs_api, cs_worker;
grant execute on function public.cs_set_workspace_funding(uuid, text), public.cs_workspace_funding_state(uuid, text),
  public.cs_run_payer(uuid, uuid) to cs_api;
