-- CS-12 (Part B of ENG-12): work starts at use time; the scheduler becomes a safety net.
--
-- Enqueue into public.cs_work_items fires one asynchronous net.http_post (pg_net: queued in the
-- enqueueing transaction, sent after commit, discarded on rollback) at /api/internal/worker.
--   * Never fails or blocks the enqueue: every error is caught, recorded in cs_private.cs_kick_failures.
--   * Coalesced: at most one kick per workspace per window (setting worker_kick_window_seconds).
--   * Secret = vault secret `cs_worker_cron_secret`; base URL = cs_platform_settings.worker_base_url
--     (never a literal here). Kicking is OFF until the operator sets worker_kick_enabled = 'true'.
--   * cs_private.cs_safety_net_sweep() is what the slow pg_cron job `cs-worker-drain` runs
--     (scripts/ops/sql/cs_worker_dispatch.sql schedules it; this file schedules nothing).
-- Idempotent: safe to apply repeatedly. No grants to anon/authenticated/cs_api/cs_worker.

-- -----------------------------------------------------------------------------
-- 1. Platform settings (config, not code)
-- -----------------------------------------------------------------------------
create table if not exists cs_private.cs_platform_settings (
  key        text primary key,
  value      text not null,
  updated_at timestamptz not null default now(),
  constraint cs_platform_settings_valid check (
    case key
      when 'worker_kick_enabled'        then value in ('true', 'false')
      when 'worker_base_url'            then value ~ '^https://[^/[:space:]]+$'
      when 'worker_kick_window_seconds' then value ~ '^[0-9]{1,4}$'
      when 'worker_safety_net_seconds'  then value ~ '^[0-9]{2,5}$'
      when 'worker_sweep_grace_seconds' then value ~ '^[0-9]{1,4}$'
      else false end)
);
insert into cs_private.cs_platform_settings (key, value) values
  ('worker_kick_enabled', 'false'),
  ('worker_kick_window_seconds', '2'),
  ('worker_safety_net_seconds', '120'),
  ('worker_sweep_grace_seconds', '20')
on conflict (key) do nothing;

create table if not exists cs_private.cs_kick_state (
  scope        text primary key,
  last_kick_at timestamptz not null,
  kicks        bigint not null default 0,
  suppressed   bigint not null default 0
);
create table if not exists cs_private.cs_kick_requests (
  request_id bigint primary key,
  scope      text not null,
  sent_at    timestamptz not null default clock_timestamp(),
  checked    boolean not null default false
);
create table if not exists cs_private.cs_kick_failures (
  id             bigint generated always as identity primary key,
  environment_id text,
  workspace_id   uuid,
  reason         text not null,
  detail         text,
  created_at     timestamptz not null default clock_timestamp()
);
create index if not exists cs_kick_failures_created_idx on cs_private.cs_kick_failures(created_at);

do $$
declare t text;
begin
  foreach t in array array['cs_platform_settings', 'cs_kick_state', 'cs_kick_requests', 'cs_kick_failures'] loop
    execute format('alter table cs_private.%I enable row level security', t);
    execute format('alter table cs_private.%I force row level security', t);
    execute format('revoke all on cs_private.%I from public, anon, authenticated, cs_api, cs_worker', t);
  end loop;
end $$;

-- -----------------------------------------------------------------------------
-- 2. Helpers
-- -----------------------------------------------------------------------------
create or replace function cs_private.cs_setting(p_key text, p_default text default null)
returns text language sql stable security definer set search_path = public, pg_temp as $$
  select coalesce((select s.value from cs_private.cs_platform_settings s where s.key = p_key), p_default);
$$;

create or replace function cs_private.cs_record_kick_failure(
  p_environment_id text, p_workspace_id uuid, p_reason text, p_detail text)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
begin
  insert into cs_private.cs_kick_failures (environment_id, workspace_id, reason, detail)
  values (p_environment_id, p_workspace_id, left(p_reason, 80), left(p_detail, 500));
exception when others then
  raise warning 'cs_kick_failure_unrecorded: % (%)', p_reason, sqlerrm;
end $$;

-- -----------------------------------------------------------------------------
-- 3. The kick. Returns sent | coalesced | disabled | failed. NEVER raises.
-- -----------------------------------------------------------------------------
create or replace function cs_private.cs_kick_worker(
  p_environment_id text, p_workspace_id uuid, p_reason text default 'enqueue')
returns text
language plpgsql security definer
set search_path = public, pg_temp
set lock_timeout = '200ms'
as $$
declare
  v_scope   text := coalesce('ws:' || p_workspace_id::text, 'env:' || p_environment_id);
  v_window  integer;
  v_base    text;
  v_secret  text;
  v_rows    integer;
  v_req     bigint;
begin
  if coalesce(cs_private.cs_setting('worker_kick_enabled', 'false'), 'false') <> 'true' then
    return 'disabled';
  end if;
  if not exists (select 1 from cs_private.cs_runtime_limits l
                  where l.environment_id = p_environment_id and l.dispatch_enabled) then
    return 'disabled';   -- the worker would refuse with dispatch_disabled; not a failure
  end if;
  v_window := coalesce(cs_private.cs_setting('worker_kick_window_seconds', '2'), '2')::integer;

  begin   -- subtransaction: any failure below rolls back the coalescing stamp, never the caller
    if not pg_try_advisory_xact_lock(hashtextextended('cs_kick:' || v_scope, 0)) then
      return 'coalesced';   -- another transaction is kicking this scope right now
    end if;
    insert into cs_private.cs_kick_state as ks (scope, last_kick_at, kicks)
    values (v_scope, clock_timestamp(), 1)
    on conflict (scope) do update
      set last_kick_at = clock_timestamp(), kicks = ks.kicks + 1
      where ks.last_kick_at <= clock_timestamp() - make_interval(secs => v_window);
    get diagnostics v_rows = row_count;
    if v_rows = 0 then
      update cs_private.cs_kick_state set suppressed = suppressed + 1 where scope = v_scope;
      return 'coalesced';
    end if;

    v_base := cs_private.cs_setting('worker_base_url');
    if v_base is null then
      raise exception 'kick_unconfigured: worker_base_url is not set' using errcode = 'CS503';
    end if;
    if to_regclass('vault.decrypted_secrets') is null then
      raise exception 'kick_unconfigured: vault is unavailable' using errcode = 'CS503';
    end if;
    execute 'select decrypted_secret from vault.decrypted_secrets where name = $1 limit 1'
      into v_secret using 'cs_worker_cron_secret';
    if v_secret is null then
      raise exception 'kick_unconfigured: vault secret cs_worker_cron_secret is missing' using errcode = 'CS503';
    end if;
    if to_regprocedure('net.http_post(text,jsonb,jsonb,jsonb,integer)') is null then
      raise exception 'pg_net_unavailable: net.http_post is not installed' using errcode = 'CS503';
    end if;
    execute 'select net.http_post(url := $1, body := $2, headers := $3, timeout_milliseconds := $4)'
      into v_req
      using v_base || '/api/internal/worker',
            jsonb_build_object('source', 'db-kick', 'reason', p_reason),
            jsonb_build_object('Content-Type', 'application/json', 'Authorization', 'Bearer ' || v_secret),
            5000;
    if v_req is not null then
      insert into cs_private.cs_kick_requests (request_id, scope) values (v_req, v_scope)
      on conflict do nothing;
    end if;
    return 'sent';
  exception when others then
    perform cs_private.cs_record_kick_failure(p_environment_id, p_workspace_id,
      'kick_error:' || sqlstate, sqlerrm);
    return 'failed';
  end;
exception when others then
  -- even the guard clauses above must never reach the caller
  perform cs_private.cs_record_kick_failure(p_environment_id, p_workspace_id, 'kick_guard:' || sqlstate, sqlerrm);
  return 'failed';
end $$;

-- -----------------------------------------------------------------------------
-- 4. Trigger on the work queue (covers every enqueue path: B1/B2/B3 inserts and re-queues)
-- -----------------------------------------------------------------------------
create or replace function cs_private.cs_work_items_kick_trg() returns trigger
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  begin
    if tg_op = 'INSERT' then
      if new.state = 'queued' and new.due_at <= now() then
        perform cs_private.cs_kick_worker(new.environment_id, new.workspace_id, 'enqueue');
      end if;
    elsif old.state = 'leased' and new.state in ('done', 'queued') then
      -- chain: a finished item hands over to the next due item without waiting for the safety net
      if exists (select 1 from public.cs_work_items w
                  where w.environment_id = new.environment_id and w.state in ('queued', 'retry_wait')
                    and w.due_at <= now() and w.id <> new.id) then
        perform cs_private.cs_kick_worker(new.environment_id, new.workspace_id, 'chain');
      end if;
    elsif new.state = 'queued' and old.state is distinct from 'queued' and new.due_at <= now() then
      perform cs_private.cs_kick_worker(new.environment_id, new.workspace_id, 'requeue');
    end if;
  exception when others then
    perform cs_private.cs_record_kick_failure(new.environment_id, new.workspace_id,
      'trigger_error:' || sqlstate, sqlerrm);
  end;
  return null;
end $$;

drop trigger if exists cs_work_items_kick on public.cs_work_items;
create trigger cs_work_items_kick after insert or update of state on public.cs_work_items
  for each row execute function cs_private.cs_work_items_kick_trg();

-- -----------------------------------------------------------------------------
-- 5. Safety-net sweep (the only thing the slow `cs-worker-drain` cron job runs)
--    (a) due work older than the grace window that nobody claimed (kick lost / endpoint down)
--    (b) expired leases of non-paid stages (mirrors cs_claim_work: paid stages are never auto-reclaimed)
--    plus: record non-2xx pg_net responses of earlier kicks, prune bookkeeping.
-- -----------------------------------------------------------------------------
create or replace function cs_private.cs_safety_net_sweep() returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_grace   integer := coalesce(cs_private.cs_setting('worker_sweep_grace_seconds', '20'), '20')::integer;
  v_env     text;
  v_kicks   jsonb := '{}'::jsonb;
  v_result  text;
  v_failed  integer := 0;
  r         record;
begin
  if to_regclass('net._http_response') is not null then
    for r in select q.request_id, q.scope from cs_private.cs_kick_requests q
              where not q.checked and q.sent_at < clock_timestamp() - interval '10 seconds' loop
      declare v_status integer; v_err text; v_found boolean;
      begin
        execute 'select status_code, error_msg, true from net._http_response where id = $1'
          into v_status, v_err, v_found using r.request_id;
        if coalesce(v_found, false) then
          if v_err is not null or v_status is null or v_status not between 200 and 299 then
            perform cs_private.cs_record_kick_failure(null, null, 'kick_http_' || coalesce(v_status::text, 'error'),
              coalesce(v_err, 'worker endpoint answered ' || v_status) || ' scope=' || r.scope);
            v_failed := v_failed + 1;
          end if;
          update cs_private.cs_kick_requests set checked = true where request_id = r.request_id;
        end if;
      exception when others then
        perform cs_private.cs_record_kick_failure(null, null, 'sweep_response_check:' || sqlstate, sqlerrm);
      end;
    end loop;
  end if;
  delete from cs_private.cs_kick_requests where sent_at < now() - interval '1 day';
  delete from cs_private.cs_kick_failures where created_at < now() - interval '30 days';

  for v_env in
    select distinct w.environment_id
      from public.cs_work_items w
      join cs_private.cs_runtime_limits l on l.environment_id = w.environment_id and l.dispatch_enabled
     where w.attempt_count < w.max_attempts
       and ((w.state in ('queued', 'retry_wait') and w.due_at <= now() - make_interval(secs => v_grace))
         or (w.state = 'leased' and w.lease_until <= now() and w.stage <> all(cs_private.cs_paid_stages())))
  loop
    v_result := cs_private.cs_kick_worker(v_env, null, 'safety_net');
    v_kicks := v_kicks || jsonb_build_object(v_env, v_result);
  end loop;
  return jsonb_build_object('kicked', v_kicks, 'http_failures_recorded', v_failed);
end $$;

do $$
declare f text;
begin
  foreach f in array array[
    'cs_private.cs_setting(text,text)',
    'cs_private.cs_record_kick_failure(text,uuid,text,text)',
    'cs_private.cs_kick_worker(text,uuid,text)',
    'cs_private.cs_work_items_kick_trg()',
    'cs_private.cs_safety_net_sweep()'] loop
    execute format('revoke all on function %s from public, anon, authenticated, cs_api, cs_worker', f);
  end loop;
end $$;
