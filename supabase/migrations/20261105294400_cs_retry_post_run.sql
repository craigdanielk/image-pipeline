-- Retry finishing (2026-11-05, additive). A run whose provider part succeeded can stop in its FREE post-run chain
-- (compose_blocked / export_blocked / handoff_failed: a missing logo, a storage blip, Marketing OS unreachable). The
-- paid image is already made; today the only way forward was hand SQL. cs_retry_post_run lets the workspace's owner or
-- manager re-queue exactly the blocked stage of one canvas:
--   compose_blocked -> compose_image   (post.compose.<canvas>)
--   export_blocked  -> publish_export  (post.export.<canvas>)
--   handoff_failed  -> deliver_handoff (post.handoff.<canvas>)
-- It never touches provider nodes, never reserves money and never calls the provider: only a new post-stage work item
-- is queued (fresh attempts, new dedupe key per retry) and the canvas returns to `queued`. Idempotent: a canvas that is
-- not blocked (already re-queued, running or delivered) is returned unchanged with retried = false. Audited
-- (cs_audit_events `post_run.retry`) and visible in run events (`post_run.retry_requested`).
-- Shared migration table with Marketing OS: CS slot ...29xxxx.

create table if not exists public.cs_post_run_retries (
  id              uuid primary key default gen_random_uuid(),
  workspace_id    uuid not null,
  run_id          uuid not null,
  canvas_node_id  text not null,
  from_state      text not null check (from_state in ('compose_blocked', 'export_blocked', 'handoff_failed')),
  from_reason     text,
  stage           text not null check (stage in ('compose_image', 'publish_export', 'deliver_handoff')),
  work_item_id    uuid not null,
  requested_by    uuid not null,
  requested_at    timestamptz not null default clock_timestamp(),
  foreign key (workspace_id, run_id, canvas_node_id) references public.cs_post_run_state(workspace_id, run_id, canvas_node_id)
);
create index if not exists cs_post_run_retries_canvas_idx on public.cs_post_run_retries(workspace_id, run_id, canvas_node_id);
alter table public.cs_post_run_retries enable row level security;
alter table public.cs_post_run_retries force row level security;
revoke all on table public.cs_post_run_retries from public, anon, authenticated, cs_api, cs_worker;

create or replace function public.cs_retry_post_run(p_workspace_id uuid, p_run_id uuid, p_canvas_node_id text)
returns table(run_id uuid, canvas_node_id text, state text, stage text, retried boolean)
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_require_actor();
  v_run public.cs_runs;
  st public.cs_post_run_state;
  v_stage text;
  v_node text;
  v_n integer;
  v_item uuid;
begin
  if not cs_private.cs_can(p_workspace_id, 'read') then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  if not cs_private.cs_can(p_workspace_id, 'manage') then       -- owner / manager only
    perform cs_private.cs_b3_refuse('capability_denied', null, 'CS403');
  end if;
  select * into v_run from public.cs_runs r where r.workspace_id = p_workspace_id and r.id = p_run_id;
  if v_run.id is null then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  select * into st from public.cs_post_run_state s
   where s.workspace_id = p_workspace_id and s.run_id = p_run_id and s.canvas_node_id = p_canvas_node_id for update;
  if st.run_id is null then
    perform cs_private.cs_b3_refuse('resource_unavailable', 'no finishing step for this image', 'CS404');
  end if;
  v_stage := case st.state when 'compose_blocked' then 'compose_image' when 'export_blocked' then 'publish_export'
                           when 'handoff_failed' then 'deliver_handoff' end;
  if v_stage is null then                                       -- nothing blocked: idempotent no-op
    return query select p_run_id, p_canvas_node_id, st.state, null::text, false;
    return;
  end if;
  v_node := case v_stage when 'compose_image' then 'post.compose.' when 'publish_export' then 'post.export.'
                         else 'post.handoff.' end || p_canvas_node_id;
  -- A live item for this stage (should not exist while blocked) means a retry is already under way.
  if exists (select 1 from public.cs_work_items w
              where w.workspace_id = p_workspace_id and w.run_id = p_run_id and w.node_id = v_node
                and w.state in ('queued', 'leased', 'retry_wait')) then
    return query select p_run_id, p_canvas_node_id, st.state, v_stage, false;
    return;
  end if;
  select count(*) + 1 into v_n from public.cs_post_run_retries x
   where x.workspace_id = p_workspace_id and x.run_id = p_run_id and x.canvas_node_id = p_canvas_node_id;
  insert into public.cs_work_items (workspace_id, environment_id, run_id, node_id, stage, stage_revision, dedupe_key,
                                    max_attempts, payload_ref)
  values (p_workspace_id, v_run.environment_id, p_run_id, v_node, v_stage::public.cs_work_stage, v_n + 1,
          'post:' || v_stage || ':' || p_run_id || ':' || p_canvas_node_id || ':retry:' || v_n, 3,
          jsonb_build_object('canvas_node_id', p_canvas_node_id))
  returning id into v_item;
  update public.cs_run_nodes n set state = 'queued'
   where n.workspace_id = p_workspace_id and n.run_id = p_run_id and n.node_id = v_node;
  update public.cs_post_run_state s set state = 'queued', reason = null,
         detail = jsonb_build_object('retry_of', st.state, 'retry_reason', st.reason, 'retry', v_n),
         updated_at = clock_timestamp()
   where s.workspace_id = p_workspace_id and s.run_id = p_run_id and s.canvas_node_id = p_canvas_node_id;
  insert into public.cs_post_run_retries (workspace_id, run_id, canvas_node_id, from_state, from_reason, stage,
                                          work_item_id, requested_by)
  values (p_workspace_id, p_run_id, p_canvas_node_id, st.state, st.reason, v_stage, v_item, v_actor);
  perform cs_private.cs_append_run_event(p_workspace_id, p_run_id, 'post_run.retry_requested',
    jsonb_build_object('canvas_node_id', p_canvas_node_id, 'stage', v_stage, 'from_state', st.state,
                       'from_reason', st.reason, 'actor_id', v_actor, 'retry', v_n));
  insert into public.cs_audit_events (account_id, workspace_id, actor_id, action, affected_refs)
  select ws.account_id, p_workspace_id, v_actor, 'post_run.retry',
         jsonb_build_array(jsonb_build_object('kind', 'run', 'id', p_run_id, 'canvas_node_id', p_canvas_node_id,
                                              'stage', v_stage, 'from_state', st.state))
    from public.cs_workspaces ws where ws.id = p_workspace_id;
  return query select p_run_id, p_canvas_node_id, 'queued'::text, v_stage, true;
end $$;

revoke all on function public.cs_retry_post_run(uuid, uuid, text) from public, anon, authenticated, cs_worker;
grant execute on function public.cs_retry_post_run(uuid, uuid, text) to cs_api, service_role;
