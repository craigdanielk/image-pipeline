-- CS-INT2: cs_runtime_limits.paid_provider_enabled is the single spend switch (production value: false).
-- Creative Studio is on-demand: free stages (import, compile, composition...) may start from the CS-12
-- kick; a provider-spending stage needs an explicit admission (cs_admit_run, Produce click) AND this flag.
--   1. cs_claim_work never leases provider_submit / author_treatment while the flag is false.
--   2. Defence in depth: no attempt row may be created, and no attempt may move to 'dispatching',
--      while the flag is false (refusal code paid_provider_disabled, SQLSTATE CS503).
-- Reconcile/cancel of attempts that already exist are not blocked (they settle, never newly spend).
-- Idempotent.
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
         -- spend switch: new paid work is never leased while paid_provider_enabled is false
         and (v_limits.paid_provider_enabled or w.stage not in ('provider_submit', 'author_treatment'))
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

create or replace function cs_private.cs_paid_provider_gate() returns trigger
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_env text; v_enabled boolean;
begin
  if tg_op = 'UPDATE' and not (new.state = 'dispatching' and old.state is distinct from 'dispatching') then
    return new;
  end if;
  select r.environment_id into v_env from public.cs_runs r
   where r.workspace_id = new.workspace_id and r.id = new.run_id;
  select l.paid_provider_enabled into v_enabled from cs_private.cs_runtime_limits l where l.environment_id = v_env;
  if not coalesce(v_enabled, false) then
    raise exception 'paid_provider_disabled: paid provider work is switched off for this environment'
      using errcode = 'CS503';
  end if;
  return new;
end $$;
revoke all on function cs_private.cs_paid_provider_gate() from public, anon, authenticated, cs_api, cs_worker;

drop trigger if exists cs_attempts_paid_gate on public.cs_attempts;
create trigger cs_attempts_paid_gate before insert or update of state on public.cs_attempts
  for each row execute function cs_private.cs_paid_provider_gate();
