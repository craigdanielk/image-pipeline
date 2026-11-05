-- Retry finishing (2026-11-05, additive). A run whose provider part succeeded can stop in its FREE post-run chain
-- (compose_blocked / export_blocked / handoff_failed: a missing logo, a storage blip, Marketing OS unreachable). The
-- paid image is already made; today the only way forward was hand SQL. cs_retry_post_run lets the workspace's owner or
-- manager re-queue exactly the blocked stage of one canvas:
--   compose_blocked -> compose_image   (post.compose.<canvas>)
--   export_blocked  -> publish_export  (post.export.<canvas>)
--   handoff_failed  -> deliver_handoff (post.handoff.<canvas>)
-- and, for an image-library image already delivered (handoff_delivered), RE-FINISH it: compose again from the same
-- generated image (e.g. after a layout fix), export, and hand back a NEW handoff (a new MOS asset version):
--   handoff_delivered (library only) -> compose_image, with cs_post_run_state.refinish + 1
-- A re-finish gets new composition (next revision) / export / handoff identities (runtime keys them by `refinish`), and
-- cs_post_finish (replaced below from 20261031240100, only the next-stage dedupe key changes) chains its export and
-- hand-back as fresh work items instead of colliding with the first finish's.
-- It never touches provider nodes, never reserves money and never calls the provider: only a new post-stage work item
-- is queued (fresh attempts, new dedupe key per retry) and the canvas returns to `queued`. Idempotent: a canvas that is
-- not blocked (already re-queued, running or delivered) is returned unchanged with retried = false. Audited
-- (cs_audit_events `post_run.retry`) and visible in run events (`post_run.retry_requested`).
-- Shared migration table with Marketing OS: CS slot ...29xxxx.

alter table public.cs_post_run_state add column if not exists refinish integer not null default 0 check (refinish >= 0);

create table if not exists public.cs_post_run_retries (
  id              uuid primary key default gen_random_uuid(),
  workspace_id    uuid not null,
  run_id          uuid not null,
  canvas_node_id  text not null,
  from_state      text not null check (from_state in ('compose_blocked', 'export_blocked', 'handoff_failed', 'handoff_delivered')),
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
  v_refinish integer;
  v_kind text;
  v_suffix text;
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
  v_refinish := st.refinish;
  if st.state = 'handoff_delivered' then
    select q.payload -> 'origin' ->> 'kind' into v_kind
      from public.cs_execution_plans pl
      join public.cs_creative_request_versions q on q.workspace_id = pl.workspace_id and q.version_id = pl.request_version_id
     where pl.workspace_id = p_workspace_id and pl.payload ->> 'version_id' = v_run.plan_version_id::text;
    if v_kind = 'marketing-os-library' then                     -- re-finish a delivered library image (free)
      v_stage := 'compose_image';
      v_refinish := st.refinish + 1;
    end if;
  end if;
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
  v_suffix := case when v_refinish > 0 then ':refinish:' || v_refinish else '' end;
  insert into public.cs_work_items (workspace_id, environment_id, run_id, node_id, stage, stage_revision, dedupe_key,
                                    max_attempts, payload_ref)
  values (p_workspace_id, v_run.environment_id, p_run_id, v_node, v_stage::public.cs_work_stage, v_n + 1,
          'post:' || v_stage || ':' || p_run_id || ':' || p_canvas_node_id || v_suffix || ':retry:' || v_n, 3,
          jsonb_build_object('canvas_node_id', p_canvas_node_id))
  returning id into v_item;
  update public.cs_run_nodes n set state = 'queued'
   where n.workspace_id = p_workspace_id and n.run_id = p_run_id and n.node_id = v_node;
  if v_refinish > st.refinish then                              -- the later links run again after the new compose
    update public.cs_run_nodes n set state = 'pending'
     where n.workspace_id = p_workspace_id and n.run_id = p_run_id
       and n.node_id in ('post.export.' || p_canvas_node_id, 'post.handoff.' || p_canvas_node_id);
  end if;
  update public.cs_post_run_state s set state = 'queued', reason = null, refinish = v_refinish,
         detail = jsonb_build_object('retry_of', st.state, 'retry_reason', st.reason, 'retry', v_n, 'refinish', v_refinish),
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

-- cs_post_finish: 20261031240100 body; the only change is the next stage's dedupe key, which carries `:refinish:<n>`
-- for a re-finished canvas so its export / hand-back are new work items (the first finish's items stay done).
create or replace function public.cs_post_finish(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_state text, p_reason text, p_detail jsonb,
  p_patch jsonb, p_next_stage text, p_release_id text)
returns text language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
  v_canvas text;
  v_next_node text;
  v_failed boolean := p_state in ('compose_blocked', 'export_blocked', 'handoff_blocked', 'handoff_failed');
  v_seq bigint;
begin
  v := cs_private.cs_post_lease(p_work_item_id, p_lease_token, p_fencing_token);
  v_canvas := v.payload_ref ->> 'canvas_node_id';
  if p_next_stage is not null and (v_failed or p_next_stage not in ('publish_export', 'deliver_handoff')) then
    raise exception 'invalid_next_stage' using errcode = 'CS422';
  end if;
  update public.cs_post_run_state s
     set state = p_state, reason = p_reason, detail = coalesce(p_detail, '{}'::jsonb),
         composition_version_id = coalesce((p_patch ->> 'composition_version_id')::uuid, s.composition_version_id),
         render_sha256 = coalesce(p_patch ->> 'render_sha256', s.render_sha256),
         composed_at = case when p_patch ? 'composition_version_id' then clock_timestamp() else s.composed_at end,
         export_version_id = coalesce((p_patch ->> 'export_version_id')::uuid, s.export_version_id),
         exported_at = case when p_patch ? 'export_version_id' then clock_timestamp() else s.exported_at end,
         handoff_id = coalesce((p_patch ->> 'handoff_id')::uuid, s.handoff_id),
         updated_at = clock_timestamp()
   where s.workspace_id = v.workspace_id and s.run_id = v.run_id and s.canvas_node_id = v_canvas;
  if not found then
    raise exception 'post_run_state_missing' using errcode = 'CS404';
  end if;
  insert into public.cs_stage_results (workspace_id, work_item_id, fencing_token, result_kind, output_refs,
                                       receipt_sha256, release_id)
  values (v.workspace_id, v.id, v.fencing_token, left(regexp_replace(p_state, '[^a-z0-9_]', '_', 'g'), 39), '[]'::jsonb,
          encode(sha256(convert_to(coalesce(p_detail, '{}'::jsonb)::text || coalesce(p_reason, ''), 'UTF8')), 'hex'),
          p_release_id);
  update public.cs_work_items w
     set state = 'done', lease_token = null, lease_until = null, lease_owner = null, failure_code = null
   where w.id = v.id;
  v_seq := cs_private.cs_append_run_event(v.workspace_id, v.run_id, 'post_run.' || p_state,
    jsonb_build_object('canvas_node_id', v_canvas, 'stage', v.stage, 'reason', p_reason));
  update public.cs_run_nodes n
     set state = case when v_failed then 'failed'::public.cs_node_state else 'succeeded'::public.cs_node_state end,
         last_event_sequence = v_seq
   where n.workspace_id = v.workspace_id and n.run_id = v.run_id and n.node_id = v.node_id;
  if p_next_stage is not null then
    v_next_node := case p_next_stage when 'publish_export' then 'post.export.' else 'post.handoff.' end || v_canvas;
    update public.cs_run_nodes n set state = 'queued'
     where n.workspace_id = v.workspace_id and n.run_id = v.run_id and n.node_id = v_next_node;
    insert into public.cs_work_items (workspace_id, environment_id, run_id, node_id, stage, dedupe_key, max_attempts,
                                      payload_ref)
    values (v.workspace_id, v.environment_id, v.run_id, v_next_node, p_next_stage::public.cs_work_stage,
            'post:' || p_next_stage || ':' || v.run_id || ':' || v_canvas
              || coalesce((select ':refinish:' || s.refinish from public.cs_post_run_state s
                            where s.workspace_id = v.workspace_id and s.run_id = v.run_id and s.canvas_node_id = v_canvas
                              and s.refinish > 0), ''), 3,
            jsonb_build_object('canvas_node_id', v_canvas))
    on conflict (environment_id, dedupe_key) do nothing;
  end if;
  return p_state;
end $$;

