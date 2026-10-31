-- CS-15 (ENG-15 Part CS): MOS -> Creative Studio signed service assertion support.
--   * cs_private.cs_assertion_nonces  insert-only, unique nonce, expiry-swept by the existing safety-net job
--   * public.cs_consume_assertion_nonce(nonce, expires_at) -> true exactly once per nonce (concurrent-safe)
--   * public.cs_assertion_binding_ok(workspace, external_workspace) -> binding exists, active, maps to workspace
--   * cs_safety_net_sweep() additionally deletes expired nonces
-- Not applied by this change; ordered after 20261031190300.
create table if not exists cs_private.cs_assertion_nonces (
  nonce       text primary key check (length(nonce) between 16 and 128),
  expires_at  timestamptz not null,
  consumed_at timestamptz not null default now()
);
create index if not exists cs_assertion_nonces_expiry on cs_private.cs_assertion_nonces(expires_at);
alter table cs_private.cs_assertion_nonces enable row level security;
alter table cs_private.cs_assertion_nonces force row level security;
revoke all on cs_private.cs_assertion_nonces from public, anon, authenticated, cs_api, cs_worker;

create or replace function cs_private.cs_assertion_nonces_guard() returns trigger
language plpgsql set search_path = public, pg_temp as $$
begin
  raise exception 'cs_immutable_record: assertion nonces are insert-only' using errcode = 'CS409';
end $$;
drop trigger if exists cs_assertion_nonces_insert_only on cs_private.cs_assertion_nonces;
create trigger cs_assertion_nonces_insert_only before update on cs_private.cs_assertion_nonces
  for each row execute function cs_private.cs_assertion_nonces_guard();

revoke all on function cs_private.cs_assertion_nonces_guard() from public, anon, authenticated, cs_api, cs_worker;

create or replace function public.cs_consume_assertion_nonce(p_nonce text, p_expires_at timestamptz)
returns boolean language plpgsql security definer set search_path = public, pg_temp as $$
declare v_n integer;
begin
  if p_nonce is null or length(p_nonce) not between 16 and 128 or p_expires_at is null then
    return false;
  end if;
  insert into cs_private.cs_assertion_nonces(nonce, expires_at) values (p_nonce, p_expires_at)
  on conflict (nonce) do nothing;
  get diagnostics v_n = row_count;
  return v_n = 1;
end $$;

create or replace function public.cs_assertion_binding_ok(p_workspace_id uuid, p_external_workspace_id text)
returns boolean language sql stable security definer set search_path = public, pg_temp as $$
  select exists (select 1 from public.cs_external_bindings b
                  where b.workspace_id = p_workspace_id and b.external_workspace_id = p_external_workspace_id
                    and b.authority = 'marketing-os' and b.status = 'active');
$$;

revoke all on function public.cs_consume_assertion_nonce(text, timestamptz) from public, anon, authenticated, cs_worker;
revoke all on function public.cs_assertion_binding_ok(uuid, text) from public, anon, authenticated, cs_worker;
grant execute on function public.cs_consume_assertion_nonce(text, timestamptz) to cs_api;
grant execute on function public.cs_assertion_binding_ok(uuid, text) to cs_api;

-- sweep: CS-12 body plus the nonce expiry delete
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
  delete from cs_private.cs_assertion_nonces where expires_at < now() - interval '5 minutes';

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

revoke all on function cs_private.cs_safety_net_sweep() from public, anon, authenticated, cs_api, cs_worker;
