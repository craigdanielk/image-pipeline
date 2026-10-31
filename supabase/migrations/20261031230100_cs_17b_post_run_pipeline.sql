-- CS-17b: hosted post-render pipeline (compose -> export -> MOS handoff) + actual cost.
-- Not applied by this change; ordered after 20261031220100.
--   * A finished provider node (cs_complete_ingest) queues, idempotently per (run, canvas), three FREE worker stages on
--     post-run nodes `post.compose.<canvas>` / `post.export.<canvas>` / `post.handoff.<canvas>` (stages compose_image,
--     publish_export, deliver_handoff). Post nodes never gate the provider run: cs_advance_run ignores them, so a
--     completed run stays completed whatever happens after (compose_blocked etc. are visible in cs_post_run_state).
--   * cs_post_run_state: the visible, truthful state of each canvas's post-run chain (definer-only access).
--   * Worker definer functions (cs_worker, live lease on the post stage): context read, composition save, stage finish,
--     handoff enqueue / delivery record. No new spend path: nothing here reserves or settles money.
--   * cs_get_handoff also resolves a handoff by its version_id (MOS references a handoff by version_id).
--   * cs_record_handoff_receipt (cs_api, produce): MOS's ingestion receipt, verified in the service, marks `received`.
--   * cs_get_post_run_status (cs_api, read): post-run states for run status.
-- Re-runnable: IF NOT EXISTS / OR REPLACE throughout.
do $$
begin
  if to_regprocedure('cs_private.cs_advance_run(uuid,uuid)') is null
     or to_regclass('public.cs_exports') is null or to_regclass('public.cs_execution_plans') is null then
    raise exception 'cs_17b: CS-B3, CS-B4 and CS-16 migrations are required';
  end if;
end $$;

create or replace function cs_private.cs_is_post_node(p_node_id text) returns boolean
language sql immutable set search_path = public, pg_temp as $$ select p_node_id like 'post.%' $$;

create table if not exists public.cs_post_run_state (
  workspace_id           uuid not null,
  run_id                 uuid not null,
  canvas_node_id         text not null,
  state                  text not null default 'queued' check (state in (
                           'queued', 'composed', 'compose_blocked', 'exported', 'export_blocked',
                           'handoff_offered', 'handoff_delivered', 'handoff_blocked', 'handoff_failed',
                           'handoff_not_applicable')),
  reason                 text check (reason is null or reason ~ '^[a-z][a-z0-9_:.-]{0,119}$'),
  detail                 jsonb not null default '{}'::jsonb check (jsonb_typeof(detail) = 'object'),
  composition_version_id uuid,
  render_sha256          text check (render_sha256 ~ '^[0-9a-f]{64}$'),
  composed_at            timestamptz,
  export_version_id      uuid,
  exported_at            timestamptz,
  handoff_id             uuid,
  created_at             timestamptz not null default clock_timestamp(),
  updated_at             timestamptz not null default clock_timestamp(),
  primary key (workspace_id, run_id, canvas_node_id),
  foreign key (workspace_id, run_id) references public.cs_runs(workspace_id, id),
  foreign key (workspace_id, run_id, canvas_node_id) references public.cs_run_nodes(workspace_id, run_id, node_id)
);
alter table public.cs_post_run_state enable row level security;
alter table public.cs_post_run_state force row level security;
revoke all on public.cs_post_run_state from public, anon, authenticated, cs_api, cs_worker;

-- -----------------------------------------------------------------------------
-- 1. Provider DAG ignores post-run nodes; ingest queues the post-run chain
-- -----------------------------------------------------------------------------
create or replace function cs_private.cs_advance_run(p_workspace_id uuid, p_run_id uuid)
returns public.cs_run_state language plpgsql set search_path = public, pg_temp as $$
declare
  v_run public.cs_runs;
  n record;
  v_next public.cs_run_state;
  v_inflight boolean;
  v_recon boolean;
  v_cost jsonb;
begin
  select * into v_run from public.cs_runs where workspace_id = p_workspace_id and id = p_run_id for update;
  if v_run.state in ('completed', 'failed', 'cancelled') then
    return v_run.state;
  end if;
  -- pending nodes: release when every dependency succeeded; cancel when a
  -- dependency can no longer succeed or the run is being cancelled.
  for n in select rn.node_id, s.dependencies
             from public.cs_run_nodes rn
             join public.cs_run_node_specs s using (workspace_id, run_id, node_id)
            where rn.workspace_id = p_workspace_id and rn.run_id = p_run_id and rn.state = 'pending'
            order by rn.node_id loop
    if v_run.cancel_requested_at is not null or exists (
         select 1 from public.cs_run_nodes d
          where d.workspace_id = p_workspace_id and d.run_id = p_run_id and d.node_id = any(n.dependencies)
            and d.state in ('failed', 'cancelled', 'skipped', 'authorization_blocked', 'budget_blocked')) then
      perform cs_private.cs_set_node_state(p_workspace_id, p_run_id, n.node_id, 'cancelled',
        jsonb_build_object('code', case when v_run.cancel_requested_at is not null then 'run_cancelled'
                                        else 'dependency_unavailable' end));
      perform cs_private.cs_release_unspent(r.id, 'not_dispatched')
         from public.cs_budget_reservations r
        where r.workspace_id = p_workspace_id and r.run_id = p_run_id and r.node_id = n.node_id;
    elsif not exists (
         select 1 from public.cs_run_nodes d
          where d.workspace_id = p_workspace_id and d.run_id = p_run_id and d.node_id = any(n.dependencies)
            and d.state <> 'succeeded') then
      perform cs_private.cs_set_node_state(p_workspace_id, p_run_id, n.node_id, 'queued');
      insert into public.cs_work_items (workspace_id, environment_id, run_id, node_id, stage, dedupe_key, max_attempts)
      values (p_workspace_id, v_run.environment_id, p_run_id, n.node_id, 'provider_submit',
              'submit:' || p_run_id || ':' || n.node_id, 3)
      on conflict (environment_id, dedupe_key) do nothing;
    end if;
  end loop;

  select exists (select 1 from public.cs_run_nodes where workspace_id = p_workspace_id and run_id = p_run_id
                  and not cs_private.cs_is_post_node(node_id)
                  and state in ('queued', 'running', 'waiting_provider'))
    into v_inflight;
  select exists (select 1 from public.cs_run_nodes where workspace_id = p_workspace_id and run_id = p_run_id
                  and not cs_private.cs_is_post_node(node_id)
                  and state = 'needs_reconciliation')
      or exists (select 1 from public.cs_budget_reservations where workspace_id = p_workspace_id and run_id = p_run_id
                  and billing_exception)
    into v_recon;

  v_next := case
    when v_inflight then case when v_run.cancel_requested_at is not null then 'cancel_requested'::public.cs_run_state
                              when v_run.state = 'queued' then 'queued' else 'running' end
    when v_recon then 'needs_reconciliation'
    when v_run.cancel_requested_at is not null then 'cancelled'
    when not exists (select 1 from public.cs_run_nodes where workspace_id = p_workspace_id and run_id = p_run_id
                      and not cs_private.cs_is_post_node(node_id)
                      and state <> 'succeeded') then 'completed'
    when exists (select 1 from public.cs_run_nodes where workspace_id = p_workspace_id and run_id = p_run_id
                  and not cs_private.cs_is_post_node(node_id)
                  and state = 'authorization_blocked') then 'authorization_blocked'
    else 'failed' end;

  v_cost := cs_private.cs_run_cost(p_workspace_id, p_run_id);
  if v_next is distinct from v_run.state then
    perform cs_private.cs_append_run_event(p_workspace_id, p_run_id, 'run.state_changed',
      jsonb_build_object('previous', v_run.state, 'next', v_next, 'error', null));
  end if;
  update public.cs_runs set state = v_next, cost_state = (v_cost ->> 'state')::public.cs_cost_state,
         failure_code = case when v_next in ('failed', 'needs_reconciliation', 'authorization_blocked')
                             then coalesce(failure_code, v_next::text) else failure_code end
   where workspace_id = p_workspace_id and id = p_run_id;
  return v_next;
end $$;

create or replace function public.cs_complete_ingest(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_output_refs jsonb, p_release_id text)
returns public.cs_run_state language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
  a public.cs_attempts;
  r jsonb;
  v_expected integer;
  v_seq bigint;
begin
  v := cs_private.cs_b3_lease(p_work_item_id, p_lease_token, p_fencing_token, 'ingest_original');
  select * into a from public.cs_attempts x
   where x.workspace_id = v.workspace_id and x.id = (v.payload_ref ->> 'attempt_id')::uuid for update;
  if a.id is null or a.state <> 'ingesting' then
    perform cs_private.cs_b3_refuse('attempt_not_ingesting', coalesce(a.state::text, 'missing'), 'CS409');
  end if;
  perform cs_private.cs_assert_asset_refs(v.workspace_id, p_output_refs);
  select count(*) into v_expected from cs_private.cs_attempt_outputs o
   where o.workspace_id = a.workspace_id and o.attempt_id = a.id;
  if jsonb_array_length(p_output_refs) <> v_expected then
    perform cs_private.cs_b3_refuse('outputs_incomplete', format('%s of %s outputs', jsonb_array_length(p_output_refs), v_expected));
  end if;
  -- every ref must be a version committed under THIS lease (provenance)
  if exists (select 1 from jsonb_array_elements(p_output_refs) x
              where not exists (select 1 from public.cs_asset_versions av
                                 where av.workspace_id = v.workspace_id and av.id::text = x ->> 'asset_version_id'
                                   and av.work_item_id = v.id and av.fencing_token = v.fencing_token)) then
    perform cs_private.cs_b3_refuse('output_provenance_mismatch', null, 'CS409');
  end if;
  insert into public.cs_stage_results (workspace_id, work_item_id, fencing_token, result_kind, output_refs,
    receipt_sha256, release_id)
  values (v.workspace_id, v.id, v.fencing_token, 'ingested', p_output_refs,
          encode(sha256(convert_to(p_output_refs::text, 'UTF8')), 'hex'), p_release_id);
  update public.cs_work_items set state = 'done', lease_token = null, lease_until = null, lease_owner = null,
         failure_code = null where id = v.id;
  for r in select * from jsonb_array_elements(p_output_refs) loop
    perform cs_private.cs_append_run_event(v.workspace_id, v.run_id, 'asset.persisted',
      jsonb_build_object('node_id', v.node_id, 'attempt_id', a.id, 'asset', r, 'output_port', 'image',
                         'size_bytes', (select av.size_bytes from public.cs_asset_versions av
                                         where av.workspace_id = v.workspace_id and av.id::text = r ->> 'asset_version_id')));
  end loop;
  perform cs_private.cs_attempt_transition(a.workspace_id, a.id, 'succeeded', 'outputs_persisted');
  perform cs_private.cs_set_node_state(v.workspace_id, v.run_id, v.node_id, 'succeeded');
  update public.cs_run_nodes set output_refs = p_output_refs
   where workspace_id = v.workspace_id and run_id = v.run_id and node_id = v.node_id;
  -- CS-17b: the provider output is persisted -> queue the free post-run chain for this canvas (idempotent).
  perform cs_private.cs_enqueue_post_run(v.workspace_id, v.run_id, v.node_id);
  return cs_private.cs_advance_run(v.workspace_id, v.run_id);
end $$;

create or replace function cs_private.cs_enqueue_post_run(p_workspace_id uuid, p_run_id uuid, p_canvas_node_id text)
returns void language plpgsql set search_path = public, pg_temp as $$
declare
  v_env text;
  v_stage text;
  v_stages text[] := array['compose', 'export', 'handoff'];
begin
  if cs_private.cs_is_post_node(p_canvas_node_id) then
    return;
  end if;
  select r.environment_id into v_env from public.cs_runs r where r.workspace_id = p_workspace_id and r.id = p_run_id;
  insert into public.cs_post_run_state (workspace_id, run_id, canvas_node_id)
  values (p_workspace_id, p_run_id, p_canvas_node_id)
  on conflict do nothing;
  if not found then
    return;                                   -- already queued for this (run, canvas)
  end if;
  foreach v_stage in array v_stages loop
    insert into public.cs_run_nodes (workspace_id, run_id, node_id, state)
    values (p_workspace_id, p_run_id, 'post.' || v_stage || '.' || p_canvas_node_id,
            case when v_stage = 'compose' then 'queued' else 'pending' end::public.cs_node_state)
    on conflict do nothing;
  end loop;
  insert into public.cs_work_items (workspace_id, environment_id, run_id, node_id, stage, dedupe_key, max_attempts, payload_ref)
  values (p_workspace_id, v_env, p_run_id, 'post.compose.' || p_canvas_node_id, 'compose_image',
          'post:compose:' || p_run_id || ':' || p_canvas_node_id, 3,
          jsonb_build_object('canvas_node_id', p_canvas_node_id))
  on conflict (environment_id, dedupe_key) do nothing;
end $$;

-- -----------------------------------------------------------------------------
-- 2. Worker functions (cs_worker; every call holds a live lease on a post-run stage)
-- -----------------------------------------------------------------------------
create or replace function cs_private.cs_post_lease(p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint,
                                                    p_stage text default null)
returns public.cs_work_items language plpgsql set search_path = public, pg_temp as $$
declare v public.cs_work_items;
begin
  v := cs_private.cs_lock_live_lease(p_work_item_id, p_lease_token, p_fencing_token);
  if v.stage::text not in ('compose_image', 'publish_export', 'deliver_handoff')
     or not cs_private.cs_is_post_node(v.node_id)
     or (p_stage is not null and v.stage::text <> p_stage) then
    raise exception 'wrong_stage' using errcode = 'CS409';
  end if;
  return v;
end $$;

create or replace function public.cs_post_context(p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
  r public.cs_runs;
  pl public.cs_execution_plans;
  st public.cs_post_run_state;
  v_canvas text;
  v_outputs jsonb;
  v_attempt uuid;
begin
  v := cs_private.cs_post_lease(p_work_item_id, p_lease_token, p_fencing_token);
  v_canvas := v.payload_ref ->> 'canvas_node_id';
  select * into r from public.cs_runs x where x.workspace_id = v.workspace_id and x.id = v.run_id;
  select * into pl from public.cs_execution_plans x
   where x.workspace_id = v.workspace_id and x.payload ->> 'version_id' = r.plan_version_id::text;
  select * into st from public.cs_post_run_state x
   where x.workspace_id = v.workspace_id and x.run_id = v.run_id and x.canvas_node_id = v_canvas;
  select coalesce(jsonb_agg(jsonb_build_object(
           'ref', o.ref, 'storage_key', av.storage_key, 'size_bytes', av.size_bytes, 'width', av.width,
           'height', av.height, 'name', a.label) order by o.ord), '[]'::jsonb)
    into v_outputs
    from public.cs_run_nodes rn
    cross join lateral jsonb_array_elements(rn.output_refs) with ordinality as o(ref, ord)
    join public.cs_asset_versions av on av.workspace_id = rn.workspace_id and av.id::text = o.ref ->> 'asset_version_id'
    join public.cs_assets a on a.workspace_id = av.workspace_id and a.id = av.asset_id
   where rn.workspace_id = v.workspace_id and rn.run_id = v.run_id and rn.node_id = v_canvas;
  select x.id into v_attempt from public.cs_attempts x
   where x.workspace_id = v.workspace_id and x.run_id = v.run_id and x.node_id = v_canvas and x.state = 'succeeded'
   order by x.attempt_number desc limit 1;
  return jsonb_build_object(
    'run', jsonb_build_object('id', r.id, 'requested_by', r.requested_by, 'environment_id', r.environment_id,
                              'plan_version_id', r.plan_version_id, 'state', r.state),
    'work', jsonb_build_object('stage', v.stage, 'attempt_count', v.attempt_count, 'max_attempts', v.max_attempts,
                               'canvas_node_id', v_canvas),
    'plan', pl.payload, 'plan_summary', pl.summary,
    'request', (select q.payload from public.cs_creative_request_versions q
                 where q.workspace_id = v.workspace_id and q.version_id = pl.request_version_id),
    'package', (select i.payload from public.cs_source_imports i
                 where i.workspace_id = v.workspace_id and i.version_id = pl.brand_package_version_id),
    'mirrors', (select coalesce(jsonb_agg(jsonb_build_object(
                  'source_asset_version_id', m.source_asset_version_id, 'source_sha256', m.source_sha256,
                  'source_ref', m.source_ref,
                  'ref', jsonb_build_object('authority', 'creative-studio', 'workspace_id', m.workspace_id,
                           'asset_id', av.asset_id, 'asset_version_id', av.id, 'revision', av.revision,
                           'sha256', av.sha256, 'mime_type', av.mime_type),
                  'storage_key', av.storage_key, 'size_bytes', av.size_bytes, 'width', av.width, 'height', av.height,
                  'name', a.label) order by m.source_asset_version_id), '[]'::jsonb)
                  from public.cs_source_asset_mirrors m
                  join public.cs_asset_versions av on av.workspace_id = m.workspace_id and av.id = m.asset_version_id
                  join public.cs_assets a on a.workspace_id = av.workspace_id and a.id = av.asset_id
                 where m.workspace_id = v.workspace_id),
    'outputs', v_outputs,
    'attempt_id', v_attempt,
    'provider_receipts', (select coalesce(jsonb_agg(jsonb_build_object('id', pr.id, 'provider', pr.provider)
                                                    order by pr.observed_at, pr.id), '[]'::jsonb)
                            from public.cs_provider_receipts pr
                           where pr.workspace_id = v.workspace_id and pr.run_id = v.run_id and pr.node_id = v_canvas),
    'cost', cs_private.cs_run_cost(v.workspace_id, v.run_id),
    'binding', (select b.external_workspace_id from public.cs_external_bindings b
                 where b.workspace_id = v.workspace_id and b.authority = 'marketing-os' and b.status = 'active'),
    'state', to_jsonb(st),
    'composition', (select c.payload from public.cs_composition_versions c
                     where c.workspace_id = v.workspace_id and c.version_id = st.composition_version_id),
    'existing_export', (select e.manifest from public.cs_exports e
                         where e.workspace_id = v.workspace_id and e.run_id = v.run_id
                           and e.composition_version_id = st.composition_version_id
                         order by e.created_at, e.version_id limit 1),
    'handoff', (select jsonb_build_object('payload', h.payload, 'state', h.state, 'receipt', h.receipt_ref)
                  from public.cs_handoffs h where h.workspace_id = v.workspace_id and h.id = st.handoff_id));
end $$;

create or replace function public.cs_post_save_composition(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_payload jsonb, p_semantic_sha256 text,
  p_input_asset_refs jsonb, p_runtime_principal text)
returns table (version_id uuid, replayed boolean)
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
  r public.cs_runs;
  v_existing text;
  v_account uuid;
begin
  v := cs_private.cs_post_lease(p_work_item_id, p_lease_token, p_fencing_token, 'compose_image');
  select * into r from public.cs_runs x where x.workspace_id = v.workspace_id and x.id = v.run_id;
  if p_runtime_principal is null or length(p_runtime_principal) = 0 then
    raise exception 'runtime_principal_required' using errcode = 'CS422';
  end if;
  if p_payload ->> 'workspace_id' is distinct from v.workspace_id::text
     or p_payload ->> 'created_by' is distinct from r.requested_by::text then
    raise exception 'scope_mismatch' using errcode = 'CS403';
  end if;
  select c.sha256 into v_existing from public.cs_composition_versions c
   where c.workspace_id = v.workspace_id and c.version_id::text = p_payload ->> 'version_id';
  if found then
    if v_existing <> p_payload ->> 'sha256' then
      raise exception 'composition_version_conflict' using errcode = 'CS409';
    end if;
    return query select (p_payload ->> 'version_id')::uuid, true;
    return;
  end if;
  perform cs_private.cs_b4_assert_asset_refs(v.workspace_id, p_input_asset_refs);
  insert into public.cs_composition_versions (workspace_id, object_id, version_id, revision, kind, payload, sha256,
                                              semantic_sha256, input_asset_refs, created_by)
  values (v.workspace_id, (p_payload ->> 'id')::uuid, (p_payload ->> 'version_id')::uuid,
          (p_payload ->> 'revision')::integer, p_payload ->> 'kind', p_payload, p_payload ->> 'sha256',
          p_semantic_sha256, p_input_asset_refs, r.requested_by);
  select w.account_id into v_account from public.cs_workspaces w where w.id = v.workspace_id;
  insert into public.cs_audit_events (workspace_id, account_id, runtime_principal, action, input_sha256, affected_refs)
  values (v.workspace_id, v_account, p_runtime_principal, 'composition.save', p_payload ->> 'sha256',
          jsonb_build_array(jsonb_build_object('kind', 'composition', 'id', p_payload ->> 'id',
                                               'version_id', p_payload ->> 'version_id')));
  return query select (p_payload ->> 'version_id')::uuid, false;
end $$;

-- Finish a post-run stage with a typed state. p_next_stage (compose_image|publish_export|deliver_handoff) queues the
-- next link; null ends the chain. A blocked/failed outcome ends the chain with its reason visible.
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
            'post:' || p_next_stage || ':' || v.run_id || ':' || v_canvas, 3,
            jsonb_build_object('canvas_node_id', v_canvas))
    on conflict (environment_id, dedupe_key) do nothing;
  end if;
  return p_state;
end $$;

-- Retries exhausted (the work item is already dead): record the typed failure so run status shows it.
create or replace function public.cs_post_record_failure(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_state text, p_reason text, p_detail jsonb,
  p_release_id text)
returns text language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if p_state not in ('compose_blocked', 'export_blocked', 'handoff_failed') then
    raise exception 'invalid_post_state' using errcode = 'CS422';
  end if;
  return public.cs_post_finish(p_work_item_id, p_lease_token, p_fencing_token, p_state, p_reason, p_detail,
                               '{}'::jsonb, null, p_release_id);
end $$;

-- Worker-side handoff enqueue: the actor is the run's requester (no unattended identity is invented), the target
-- must be this workspace's active verified Marketing OS binding, exports must exist. Idempotent per handoff id.
create or replace function public.cs_post_enqueue_handoff(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_payload jsonb, p_request_sha256 text,
  p_runtime_principal text)
returns table (handoff_id uuid, state text, replayed boolean)
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
  r public.cs_runs;
  v_id uuid := (p_payload ->> 'id')::uuid;
  v_existing record;
  v_bound text;
  e jsonb;
  i integer := 0;
  v_account uuid;
begin
  v := cs_private.cs_post_lease(p_work_item_id, p_lease_token, p_fencing_token, 'deliver_handoff');
  select * into r from public.cs_runs x where x.workspace_id = v.workspace_id and x.id = v.run_id;
  if p_payload ->> 'workspace_id' is distinct from v.workspace_id::text
     or p_payload ->> 'created_by' is distinct from r.requested_by::text then
    raise exception 'scope_mismatch' using errcode = 'CS403';
  end if;
  select h.id, h.sha256, h.state into v_existing from public.cs_handoffs h
   where h.workspace_id = v.workspace_id and h.id = v_id;
  if found then
    if v_existing.sha256 <> p_payload ->> 'sha256' then
      raise exception 'handoff_version_conflict' using errcode = 'CS409';
    end if;
    return query select v_id, v_existing.state, true;
    return;
  end if;
  select lower(b.external_workspace_id) into v_bound from public.cs_external_bindings b
   where b.workspace_id = v.workspace_id and b.authority = 'marketing-os' and b.status = 'active';
  if v_bound is null then
    raise exception 'mos_connection_required' using errcode = 'CS409';
  end if;
  if v_bound is distinct from lower(p_payload -> 'target' ->> 'workspace_id') then
    raise exception 'scope_mismatch' using errcode = 'CS403';
  end if;
  insert into public.cs_handoffs (id, workspace_id, version_id, destination_authority, external_workspace_id,
                                  idempotency_key, request_sha256, payload, sha256, created_by)
  values (v_id, v.workspace_id, (p_payload ->> 'version_id')::uuid, 'marketing-os',
          (p_payload -> 'target' ->> 'workspace_id')::uuid, (p_payload ->> 'idempotency_key')::uuid,
          p_request_sha256, p_payload, p_payload ->> 'sha256', r.requested_by);
  for e in select * from jsonb_array_elements(p_payload -> 'exports') loop
    if e ->> 'authority' is distinct from 'creative-studio' or e ->> 'workspace_id' is distinct from v.workspace_id::text
       or not exists (select 1 from public.cs_exports x
                       where x.workspace_id = v.workspace_id and x.version_id::text = e ->> 'version_id'
                         and x.object_id::text = e ->> 'id' and x.sha256 = e ->> 'sha256') then
      raise exception 'export_unavailable' using errcode = 'CS422';
    end if;
    insert into public.cs_handoff_exports (workspace_id, handoff_id, export_version_id, export_sha256, ordinal)
    values (v.workspace_id, v_id, (e ->> 'version_id')::uuid, e ->> 'sha256', i);
    i := i + 1;
  end loop;
  if i = 0 then
    raise exception 'invalid_mos_handoff' using errcode = 'CS422';
  end if;
  select w.account_id into v_account from public.cs_workspaces w where w.id = v.workspace_id;
  insert into public.cs_audit_events (workspace_id, account_id, runtime_principal, action, input_sha256, affected_refs)
  values (v.workspace_id, v_account, p_runtime_principal, 'handoff.enqueue', p_request_sha256,
          jsonb_build_array(jsonb_build_object('kind', 'handoff', 'id', v_id)));
  return query select v_id, 'queued'::text, false;
end $$;

-- Delivery bookkeeping. `received` requires MOS's verified receipt (the table CHECK ties them); a receipt is never
-- invented here. Terminal `received` is never downgraded.
create or replace function public.cs_post_record_handoff(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_handoff_id uuid, p_state text, p_receipt jsonb)
returns text language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v public.cs_work_items;
  h public.cs_handoffs;
begin
  v := cs_private.cs_post_lease(p_work_item_id, p_lease_token, p_fencing_token, 'deliver_handoff');
  if p_state not in ('delivering', 'received', 'failed', 'needs_reconciliation')
     or (p_state = 'received') <> (p_receipt is not null) then
    raise exception 'invalid_handoff_state' using errcode = 'CS422';
  end if;
  select * into h from public.cs_handoffs x where x.workspace_id = v.workspace_id and x.id = p_handoff_id for update;
  if not found then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  if h.state = 'received' then
    return h.state;
  end if;
  update public.cs_handoffs set state = p_state, receipt_ref = p_receipt
   where workspace_id = v.workspace_id and id = p_handoff_id;
  return p_state;
end $$;

-- -----------------------------------------------------------------------------
-- 3. API-side reads/writes (cs_api): handoff by id OR version_id, MOS receipt, post-run status
-- -----------------------------------------------------------------------------
create or replace function public.cs_get_handoff(p_workspace_id uuid, p_handoff_id uuid)
returns table (payload jsonb, state text, receipt_ref jsonb)
language plpgsql stable security definer set search_path = public, pg_temp as $$
begin
  perform cs_private.cs_b4_require(p_workspace_id, 'read');
  return query select h.payload, h.state, h.receipt_ref from public.cs_handoffs h
                where h.workspace_id = p_workspace_id and (h.id = p_handoff_id or h.version_id = p_handoff_id);
  if not found then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
end $$;

create or replace function public.cs_record_handoff_receipt(p_workspace_id uuid, p_handoff_id uuid, p_receipt jsonb)
returns table (state text, replayed boolean)
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_b4_require(p_workspace_id, 'produce');
  h public.cs_handoffs;
  v_account uuid;
begin
  select * into h from public.cs_handoffs x
   where x.workspace_id = p_workspace_id and (x.id = p_handoff_id or x.version_id = p_handoff_id) for update;
  if not found then
    raise exception 'resource_unavailable' using errcode = 'CS404';
  end if;
  if p_receipt ->> 'handoff_id' is distinct from h.id::text or p_receipt ->> 'handoff_sha256' is distinct from h.sha256 then
    raise exception 'receipt_mismatch' using errcode = 'CS422';
  end if;
  if h.state = 'received' then
    if h.receipt_ref ->> 'sha256' is distinct from p_receipt ->> 'sha256' then
      raise exception 'receipt_conflict' using errcode = 'CS409';
    end if;
    return query select h.state, true;
    return;
  end if;
  update public.cs_handoffs set state = 'received', receipt_ref = p_receipt where id = h.id;
  update public.cs_post_run_state s set state = 'handoff_delivered', reason = null, updated_at = clock_timestamp()
   where s.workspace_id = p_workspace_id and s.handoff_id = h.id;
  select w.account_id into v_account from public.cs_workspaces w where w.id = p_workspace_id;
  insert into public.cs_audit_events (workspace_id, account_id, actor_id, action, input_sha256, affected_refs)
  values (p_workspace_id, v_account, v_actor, 'handoff.receipt', p_receipt ->> 'sha256',
          jsonb_build_array(jsonb_build_object('kind', 'handoff', 'id', h.id)));
  return query select 'received'::text, false;
end $$;

create or replace function public.cs_get_post_run_status(p_workspace_id uuid, p_run_id uuid)
returns jsonb language plpgsql stable security definer set search_path = public, pg_temp as $$
begin
  perform cs_private.cs_b4_require(p_workspace_id, 'read');
  return coalesce((
    select jsonb_agg(jsonb_build_object(
             'canvas_node_id', s.canvas_node_id, 'state', s.state, 'reason', s.reason, 'detail', s.detail,
             'composition_version_id', s.composition_version_id, 'render_sha256', s.render_sha256,
             'export_version_id', s.export_version_id, 'exported_at', s.exported_at,
             'handoff_id', s.handoff_id,
             'handoff', (select jsonb_build_object('authority', 'creative-studio', 'workspace_id', h.workspace_id,
                                                  'kind', 'handoff', 'id', h.id, 'version_id', h.version_id,
                                                  'revision', (h.payload ->> 'revision')::integer, 'sha256', h.sha256,
                                                  'state', h.state)
                           from public.cs_handoffs h where h.workspace_id = s.workspace_id and h.id = s.handoff_id),
             'updated_at', s.updated_at) order by s.canvas_node_id)
      from public.cs_post_run_state s where s.workspace_id = p_workspace_id and s.run_id = p_run_id), '[]'::jsonb);
end $$;

-- -----------------------------------------------------------------------------
-- 4. Privileges
-- -----------------------------------------------------------------------------
do $$
declare r record;
begin
  for r in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where (n.nspname = 'public' and p.proname in ('cs_post_context', 'cs_post_save_composition', 'cs_post_finish',
                     'cs_post_record_failure', 'cs_post_enqueue_handoff', 'cs_post_record_handoff', 'cs_get_handoff',
                     'cs_record_handoff_receipt', 'cs_get_post_run_status', 'cs_complete_ingest'))
               or (n.nspname = 'cs_private' and p.proname in ('cs_is_post_node', 'cs_enqueue_post_run', 'cs_post_lease',
                     'cs_advance_run')) loop
    execute format('revoke all on function %s from public, anon, authenticated, cs_api, cs_worker', r.sig);
  end loop;
end $$;

grant execute on function
  public.cs_post_context(uuid, uuid, bigint),
  public.cs_post_save_composition(uuid, uuid, bigint, jsonb, text, jsonb, text),
  public.cs_post_finish(uuid, uuid, bigint, text, text, jsonb, jsonb, text, text),
  public.cs_post_record_failure(uuid, uuid, bigint, text, text, jsonb, text),
  public.cs_post_enqueue_handoff(uuid, uuid, bigint, jsonb, text, text),
  public.cs_post_record_handoff(uuid, uuid, bigint, uuid, text, jsonb),
  public.cs_complete_ingest(uuid, uuid, bigint, jsonb, text)
  to cs_worker;
grant execute on function
  public.cs_get_handoff(uuid, uuid),
  public.cs_record_handoff_receipt(uuid, uuid, jsonb),
  public.cs_get_post_run_status(uuid, uuid)
  to cs_api;
