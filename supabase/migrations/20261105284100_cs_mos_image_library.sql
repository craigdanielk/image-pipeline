-- Marketing OS image library (2026-11-05, additive; docs/creative-studio/contracts/compatibility.md).
-- A creative request may now come from an approved MOS image-library brief: origin kind 'marketing-os-library'
-- (no campaign concept, copy or destination; exact_copy empty). The request table admits the new origin kind and
-- cs_save_creative_request (20261031140100 body) checks the library evidence exactly like the campaign evidence:
-- active verified binding, MOS eligibility (allowed, generate, same workspace, <= 60 s) and an approval of THIS
-- LibraryPack version. Shared migration table with Marketing OS: CS slot ...28xxxx (VERSION_RANGES.md).

alter table public.cs_creative_request_versions drop constraint if exists cs_creative_request_versions_origin_kind_check;
alter table public.cs_creative_request_versions add constraint cs_creative_request_versions_origin_kind_check
  check (origin_kind in ('standalone', 'marketing-os', 'marketing-os-library'));

create or replace function public.cs_save_creative_request(
  p_workspace_id uuid, p_document jsonb, p_canonical text, p_semantic_sha256 text, p_if_match text,
  p_idempotency_key uuid, p_request_sha256 text)
returns table (version_id uuid, sha256 text, replayed boolean)
language plpgsql set search_path = public, pg_temp as $$
declare
  v_actor uuid := cs_private.cs_require_produce(p_workspace_id);
  v_prior jsonb;
  v_sha text;
  v_object uuid;
  v_rev integer;
  v_head record;
  v_pkg public.cs_source_imports;
  v_origin jsonb;
  v_elig jsonb;
begin
  v_prior := cs_private.cs_idempotency_replay(p_workspace_id, 'creative_request.save', p_idempotency_key, p_request_sha256);
  if v_prior is not null then
    return query select (v_prior ->> 'version_id')::uuid, v_prior ->> 'sha256', true;
    return;
  end if;
  v_sha := cs_private.cs_assert_document_digest(p_document, p_canonical);
  perform cs_private.cs_assert_audit(p_document, p_workspace_id, v_actor);
  if p_semantic_sha256 is null or p_semantic_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid_semantic_digest' using errcode = 'CS422';
  end if;
  v_object := (p_document ->> 'id')::uuid;
  v_rev := (p_document ->> 'revision')::int;
  select r.revision, r.sha256 into v_head from public.cs_creative_request_versions r
   where r.workspace_id = p_workspace_id and r.object_id = v_object order by r.revision desc limit 1;
  if found then
    if p_if_match is distinct from v_head.sha256 or v_rev <> v_head.revision + 1 then
      raise exception 'revision_conflict' using errcode = 'CS409';
    end if;
  elsif v_rev <> 1 or p_if_match is not null then
    raise exception 'revision_conflict' using errcode = 'CS409';
  end if;

  -- brand package: exact imported version in THIS workspace, currently eligible to author against
  select * into v_pkg from public.cs_source_imports s
   where s.workspace_id = p_workspace_id and s.version_id = (p_document #>> '{brand_package,version_id}')::uuid;
  if not found then
    raise exception 'resource_unavailable: brand package' using errcode = 'CS404';
  end if;
  if not cs_private.cs_is_version_ref(p_document -> 'brand_package')
     or p_document #>> '{brand_package,sha256}' <> v_pkg.sha256
     or p_document #>> '{brand_package,id}' <> v_pkg.object_id::text
     or (p_document #>> '{brand_package,revision}')::int <> v_pkg.revision then
    raise exception 'source_revision_changed: brand package ref does not match the imported version' using errcode = 'CS409';
  end if;
  v_elig := cs_private.cs_require_brand_eligible(p_workspace_id, v_pkg.version_id, 'author', null);

  v_origin := p_document -> 'origin';
  if v_origin ->> 'kind' = 'marketing-os' then
    if not exists (select 1 from public.cs_external_bindings b
                    where b.workspace_id = p_workspace_id and b.authority = 'marketing-os' and b.status = 'active'
                      and b.external_workspace_id = v_origin ->> 'mos_workspace_id') then
      raise exception 'scope_mismatch: marketing-os workspace is not connected' using errcode = 'CS403';
    end if;
    if v_origin #>> '{generation_eligibility,decision}' <> 'allowed'
       or v_origin #>> '{generation_eligibility,action}' <> 'generate'
       or v_origin #>> '{generation_eligibility,workspace_id}' <> v_origin ->> 'mos_workspace_id'
       or (v_origin #>> '{generation_eligibility,valid_until}')::timestamptz
          > (v_origin #>> '{generation_eligibility,checked_at}')::timestamptz + interval '60 seconds'
       or v_origin #>> '{concept_approval,decision}' <> 'approved'
       or v_origin #>> '{concept_approval,subject,version_id}' <> v_origin #>> '{concept,version_id}' then
      raise exception 'authorization_blocked: marketing-os approval/eligibility evidence does not cover this request'
        using errcode = 'CS403';
    end if;
  elsif v_origin ->> 'kind' = 'marketing-os-library' then
    -- Marketing OS image library: the same binding + eligibility rules; the approval covers the exact LibraryPack.
    if not exists (select 1 from public.cs_external_bindings b
                    where b.workspace_id = p_workspace_id and b.authority = 'marketing-os' and b.status = 'active'
                      and b.external_workspace_id = v_origin ->> 'mos_workspace_id') then
      raise exception 'scope_mismatch: marketing-os workspace is not connected' using errcode = 'CS403';
    end if;
    if v_origin #>> '{generation_eligibility,decision}' <> 'allowed'
       or v_origin #>> '{generation_eligibility,action}' <> 'generate'
       or v_origin #>> '{generation_eligibility,workspace_id}' <> v_origin ->> 'mos_workspace_id'
       or (v_origin #>> '{generation_eligibility,valid_until}')::timestamptz
          > (v_origin #>> '{generation_eligibility,checked_at}')::timestamptz + interval '60 seconds'
       or v_origin #>> '{library_approval,decision}' <> 'approved'
       or v_origin #>> '{library_approval,subject,version_id}' <> v_origin #>> '{library,version_id}'
       or jsonb_array_length(coalesce(p_document -> 'exact_copy', '[]'::jsonb)) <> 0 then
      raise exception 'authorization_blocked: marketing-os library approval/eligibility evidence does not cover this request'
        using errcode = 'CS403';
    end if;
  end if;

  insert into public.cs_creative_request_versions (workspace_id, object_id, version_id, revision, payload, sha256,
    semantic_sha256, source_refs, brand_package_version_id, origin_kind, offering_kind, created_by)
  values (p_workspace_id, v_object, (p_document ->> 'version_id')::uuid, v_rev, p_document, v_sha, p_semantic_sha256,
          jsonb_build_array(p_document -> 'brand_package', p_document #> '{offering,source}')
            || coalesce(p_document -> 'audiences', '[]'::jsonb),
          v_pkg.version_id, v_origin ->> 'kind', p_document #>> '{offering,kind}', v_actor);
  insert into public.cs_idempotency (workspace_id, actor_id, operation, key, request_sha256, result_ref)
  values (p_workspace_id, v_actor, 'creative_request.save', p_idempotency_key, p_request_sha256,
          jsonb_build_object('version_id', p_document ->> 'version_id', 'sha256', v_sha));
  perform cs_private.cs_audit(p_workspace_id, v_actor, 'creative_request.save', p_request_sha256,
    jsonb_build_array(jsonb_build_object('kind', 'creative_request', 'id', v_object, 'version_id', p_document ->> 'version_id')));
  return query select (p_document ->> 'version_id')::uuid, v_sha, false;
exception when unique_violation then
  raise exception 'revision_conflict' using errcode = 'CS409';
end $$;

