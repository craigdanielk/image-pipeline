-- Media budget read-back (2026-11-05, additive). Marketing OS saves the owner's media caps with
-- POST /accounts/{account}/budget-policies and then reads GET /workspaces/{ws}/funding to confirm them. That response
-- never carried the budget, so Marketing OS reported "The media cap was not acknowledged" and media could never be
-- prepared. cs_workspace_budget_state returns the workspace's current policy (latest effective revision) and what is
-- used or reserved inside its window (cs_budget_consumed), with the same access rules as cs_workspace_funding_state:
-- an actor, read access, and manage access (reviewers never see funding). Read-only; no table or policy change.
-- Shared migration table with Marketing OS: CS slot ...29xxxx.

create or replace function public.cs_workspace_budget_state(p_workspace_id uuid)
returns table(configured boolean, policy_id uuid, limit_micros bigint, per_run_limit_micros bigint,
              consumed_micros bigint, funding_source text, window_seconds integer, revision integer)
language plpgsql stable security definer set search_path = public, pg_temp as $$
declare
  v_policy public.cs_budget_policies;
begin
  perform cs_private.cs_require_actor();
  if not cs_private.cs_can(p_workspace_id, 'read') then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  if not cs_private.cs_can(p_workspace_id, 'manage') then   -- reviewers never see credential/funding state
    perform cs_private.cs_b3_refuse('capability_denied', null, 'CS403');
  end if;
  select p.* into v_policy from public.cs_budget_policies p
   where p.workspace_id = p_workspace_id and p.effective_at <= now()
     and (p.expires_at is null or p.expires_at > now())
   order by p.revision desc, p.effective_at desc
   limit 1;
  if v_policy.id is null then
    return query select false, null::uuid, null::bigint, null::bigint, null::bigint, null::text, null::integer, null::integer;
    return;
  end if;
  return query select (v_policy.ceiling_micros > 0 and v_policy.per_run_ceiling_micros > 0), v_policy.id,
    v_policy.ceiling_micros::bigint, v_policy.per_run_ceiling_micros::bigint, cs_private.cs_budget_consumed(v_policy),
    v_policy.funding_source::text, v_policy.window_seconds::integer, v_policy.revision::integer;
end $$;

revoke all on function public.cs_workspace_budget_state(uuid) from public, anon, authenticated;
grant execute on function public.cs_workspace_budget_state(uuid) to cs_api, service_role;
