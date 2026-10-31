-- CS-16: hosted compile — immutable execution plans, supersession, plan quotes.
-- Not applied by this change; ordered after 20261031210100.
--   * public.cs_execution_plans            insert-only, hash-pinned execution-plan-v1 + readable media pack summary
--   * public.cs_execution_plan_supersessions insert-only: an older plan replaced by a newer one (rows never change)
--   * public.cs_plan_quotes                insert-only quote records; admission from a plan requires one
--   * definer functions only (cs_api has no direct table grant); each re-checks the caller's membership
create table if not exists public.cs_execution_plans (
  workspace_id          uuid not null references public.cs_workspaces(id),
  plan_id               uuid not null,
  request_object_id     uuid not null,
  request_version_id    uuid not null,
  treatment_object_id   uuid not null,
  treatment_version_id  uuid not null,
  brand_package_version_id uuid not null,
  catalogue_sha256      text not null check (catalogue_sha256 ~ '^[0-9a-f]{64}$'),
  content_key           text not null check (content_key ~ '^[0-9a-f]{64}$'),
  revision              integer not null check (revision > 0),
  tier                  public.cs_render_tier not null,
  sha256                text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  compile_hash          text not null check (compile_hash ~ '^[0-9a-f]{64}$'),
  payload               jsonb not null check (jsonb_typeof(payload) = 'object'
                          and payload ->> 'schema_version' = 'cs-execution-plan-v1'),
  summary               jsonb not null check (jsonb_typeof(summary) = 'object'),
  created_by            uuid not null references auth.users(id),
  created_at            timestamptz not null default clock_timestamp(),
  primary key (workspace_id, plan_id),
  unique (workspace_id, request_object_id, content_key),
  unique (workspace_id, request_object_id, revision),
  check (payload ->> 'id' = plan_id::text and payload ->> 'sha256' = sha256 and payload ->> 'workspace_id' = workspace_id::text
         and (payload ->> 'revision')::int = revision)
);

create table if not exists public.cs_execution_plan_supersessions (
  workspace_id uuid not null,
  plan_id      uuid not null,
  superseded_by_plan_id uuid not null,
  created_at   timestamptz not null default clock_timestamp(),
  primary key (workspace_id, plan_id),
  foreign key (workspace_id, plan_id) references public.cs_execution_plans(workspace_id, plan_id),
  foreign key (workspace_id, superseded_by_plan_id) references public.cs_execution_plans(workspace_id, plan_id),
  check (plan_id <> superseded_by_plan_id)
);

create table if not exists public.cs_plan_quotes (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  plan_id      uuid not null,
  tier         public.cs_render_tier not null,
  quote        jsonb not null check (jsonb_typeof(quote) = 'object'),
  created_by   uuid not null references auth.users(id),
  created_at   timestamptz not null default clock_timestamp(),
  foreign key (workspace_id, plan_id) references public.cs_execution_plans(workspace_id, plan_id)
);
create index if not exists cs_plan_quotes_lookup on public.cs_plan_quotes(workspace_id, plan_id, tier, created_at desc);

do $$
declare t text;
begin
  foreach t in array array['cs_execution_plans', 'cs_execution_plan_supersessions', 'cs_plan_quotes'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('alter table public.%I force row level security', t);
    execute format('revoke all on public.%I from public, anon, authenticated, cs_api, cs_worker', t);
    execute format('drop trigger if exists %1$s_immutable on public.%1$s', t);
    execute format('create trigger %1$s_immutable before update or delete on public.%1$s for each row execute function cs_private.cs_reject_mutation()', t);
  end loop;
end $$;

-- lookup before compile: existing plan for this exact content, or the next revision number
create or replace function public.cs_plan_lookup(p_workspace_id uuid, p_request_object_id uuid, p_content_key text)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $$
declare v_plan public.cs_execution_plans; v_next integer;
begin
  perform cs_private.cs_require_actor();
  if not cs_private.cs_can(p_workspace_id, 'read') then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  if not cs_private.cs_can(p_workspace_id, 'produce') then
    perform cs_private.cs_b3_refuse('capability_denied', null, 'CS403');
  end if;
  select * into v_plan from public.cs_execution_plans
   where workspace_id = p_workspace_id and request_object_id = p_request_object_id and content_key = p_content_key;
  select coalesce(max(revision), 0) + 1 into v_next from public.cs_execution_plans
   where workspace_id = p_workspace_id and request_object_id = p_request_object_id;
  if v_plan.plan_id is not null then
    return jsonb_build_object('existing', true, 'payload', v_plan.payload, 'summary', v_plan.summary,
                              'superseded_by', (select s.superseded_by_plan_id from public.cs_execution_plan_supersessions s
                                                 where s.workspace_id = p_workspace_id and s.plan_id = v_plan.plan_id),
                              'next_revision', v_next);
  end if;
  return jsonb_build_object('existing', false, 'next_revision', v_next);
end $$;

create or replace function public.cs_save_execution_plan(
  p_workspace_id uuid, p_plan jsonb, p_summary jsonb, p_request_object_id uuid, p_request_version_id uuid,
  p_treatment_object_id uuid, p_treatment_version_id uuid, p_package_version_id uuid, p_catalogue_sha256 text,
  p_content_key text, p_tier public.cs_render_tier)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $$
declare v_actor uuid := cs_private.cs_require_actor(); v_plan_id uuid := (p_plan ->> 'id')::uuid;
        v_rev integer; v_prior record;
begin
  if not cs_private.cs_can(p_workspace_id, 'read') then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  if not cs_private.cs_can(p_workspace_id, 'produce') then
    perform cs_private.cs_b3_refuse('capability_denied', null, 'CS403');
  end if;
  perform pg_advisory_xact_lock(hashtextextended('cs_plan:' || p_request_object_id, 0));
  select * into v_prior from public.cs_execution_plans
   where workspace_id = p_workspace_id and request_object_id = p_request_object_id and content_key = p_content_key;
  if v_prior.plan_id is not null then
    return jsonb_build_object('plan_id', v_prior.plan_id, 'replayed', true);
  end if;
  select coalesce(max(revision), 0) + 1 into v_rev from public.cs_execution_plans
   where workspace_id = p_workspace_id and request_object_id = p_request_object_id;
  if v_rev <> (p_plan ->> 'revision')::integer then
    perform cs_private.cs_b3_refuse('revision_conflict', v_rev::text, 'CS409');
  end if;
  if not exists (select 1 from public.cs_source_imports i where i.workspace_id = p_workspace_id and i.version_id = p_package_version_id) then
    perform cs_private.cs_b3_refuse('brand_package_missing', null);
  end if;
  insert into public.cs_execution_plans (workspace_id, plan_id, request_object_id, request_version_id, treatment_object_id,
    treatment_version_id, brand_package_version_id, catalogue_sha256, content_key, revision, tier, sha256, compile_hash,
    payload, summary, created_by)
  values (p_workspace_id, v_plan_id, p_request_object_id, p_request_version_id, p_treatment_object_id,
    p_treatment_version_id, p_package_version_id, p_catalogue_sha256, p_content_key, v_rev, p_tier, p_plan ->> 'sha256',
    p_plan ->> 'compile_hash', p_plan, p_summary, v_actor);
  insert into public.cs_execution_plan_supersessions (workspace_id, plan_id, superseded_by_plan_id)
  select p.workspace_id, p.plan_id, v_plan_id from public.cs_execution_plans p
   where p.workspace_id = p_workspace_id and p.request_object_id = p_request_object_id and p.plan_id <> v_plan_id
     and not exists (select 1 from public.cs_execution_plan_supersessions s
                      where s.workspace_id = p.workspace_id and s.plan_id = p.plan_id);
  return jsonb_build_object('plan_id', v_plan_id, 'replayed', false);
end $$;

create or replace function public.cs_get_execution_plan(p_workspace_id uuid, p_plan_id uuid)
returns jsonb language plpgsql stable security definer set search_path = public, pg_temp as $$
declare v_plan public.cs_execution_plans;
begin
  perform cs_private.cs_require_actor();
  if not cs_private.cs_can(p_workspace_id, 'read') then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  select * into v_plan from public.cs_execution_plans where workspace_id = p_workspace_id and plan_id = p_plan_id;
  if v_plan.plan_id is null then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  return jsonb_build_object('payload', v_plan.payload, 'summary', v_plan.summary, 'tier', v_plan.tier,
    'request_object_id', v_plan.request_object_id, 'request_version_id', v_plan.request_version_id,
    'treatment_object_id', v_plan.treatment_object_id, 'treatment_version_id', v_plan.treatment_version_id,
    'brand_package_version_id', v_plan.brand_package_version_id,
    'superseded_by', (select s.superseded_by_plan_id from public.cs_execution_plan_supersessions s
                       where s.workspace_id = p_workspace_id and s.plan_id = p_plan_id));
end $$;

-- current published catalogue entries (global allowlist; no workspace data)
create or replace function public.cs_plan_catalogue()
returns jsonb language plpgsql stable security definer set search_path = public, pg_temp as $$
begin
  perform cs_private.cs_require_actor();
  return coalesce((select jsonb_agg(jsonb_build_object(
      'entry_id', c.entry_id, 'provider', c.provider, 'model_id', c.model_id, 'endpoint_key', c.endpoint_key,
      'surface', c.surface, 'tier', c.tier, 'credential_modes', to_jsonb(c.credential_modes), 'entry', c.entry,
      'price_snapshot', c.price_snapshot, 'unit_cost_micros', c.unit_cost_micros,
      'max_units_per_request', c.max_units_per_request, 'sha256', c.sha256) order by c.entry_id)
    from (select distinct on (provider, entry_id) * from public.cs_provider_catalogue_versions
           order by provider, entry_id, published_at desc, id) c
   where c.enabled and c.valid_until > clock_timestamp() and c.price_expires_at > clock_timestamp()), '[]'::jsonb);
end $$;

create or replace function public.cs_plan_credential_available(p_workspace_id uuid, p_environment_id text, p_provider text,
                                                               p_mode text)
returns boolean language plpgsql stable security definer set search_path = public, pg_temp as $$
begin
  perform cs_private.cs_require_actor();
  if not cs_private.cs_can(p_workspace_id, 'read') then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  return exists (select 1 from cs_private.cs_credentials c
                  where c.provider = p_provider and c.status = 'active' and c.environment_id = p_environment_id
                    and c.mode::text = p_mode
                    and (c.workspace_id = p_workspace_id or (p_mode = 'platform' and c.workspace_id is null)));
end $$;

create or replace function public.cs_record_plan_quote(p_workspace_id uuid, p_plan_id uuid, p_tier public.cs_render_tier,
                                                       p_quote jsonb)
returns uuid language plpgsql security definer set search_path = public, pg_temp as $$
declare v_actor uuid := cs_private.cs_require_actor(); v_id uuid;
begin
  if not cs_private.cs_can(p_workspace_id, 'produce') then
    perform cs_private.cs_b3_refuse('capability_denied', null, 'CS403');
  end if;
  insert into public.cs_plan_quotes (workspace_id, plan_id, tier, quote, created_by)
  values (p_workspace_id, p_plan_id, p_tier, p_quote, v_actor) returning id into v_id;
  return v_id;
end $$;

create or replace function public.cs_latest_plan_quote(p_workspace_id uuid, p_plan_id uuid, p_tier public.cs_render_tier)
returns jsonb language plpgsql stable security definer set search_path = public, pg_temp as $$
begin
  perform cs_private.cs_require_actor();
  if not cs_private.cs_can(p_workspace_id, 'read') then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  return (select q.quote from public.cs_plan_quotes q
           where q.workspace_id = p_workspace_id and q.plan_id = p_plan_id and q.tier = p_tier
           order by q.created_at desc, q.id desc limit 1);
end $$;

create or replace function public.cs_paid_provider_enabled(p_environment_id text)
returns boolean language sql stable security definer set search_path = public, pg_temp as $$
  select coalesce((select l.paid_provider_enabled from cs_private.cs_runtime_limits l where l.environment_id = p_environment_id), false);
$$;

do $$
declare f text;
begin
  foreach f in array array[
    'public.cs_plan_lookup(uuid, uuid, text)',
    'public.cs_save_execution_plan(uuid, jsonb, jsonb, uuid, uuid, uuid, uuid, uuid, text, text, public.cs_render_tier)',
    'public.cs_get_execution_plan(uuid, uuid)', 'public.cs_plan_catalogue()',
    'public.cs_plan_credential_available(uuid, text, text, text)',
    'public.cs_record_plan_quote(uuid, uuid, public.cs_render_tier, jsonb)',
    'public.cs_latest_plan_quote(uuid, uuid, public.cs_render_tier)', 'public.cs_paid_provider_enabled(text)'] loop
    execute format('revoke all on function %s from public, anon, authenticated, cs_worker', f);
    execute format('grant execute on function %s to cs_api', f);
  end loop;
end $$;
