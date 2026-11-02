-- CS-20: Higgsfield account-login credentials are refreshed by the worker, single-writer, sealed in place.
--
-- Higgsfield refresh tokens rotate on every use and an already-used one revokes the whole login. So the worker
-- (1) locks the attempt's active workspace credential row, (2) refreshes over HTTP while holding the lock,
-- (3) re-seals the new login into the SAME row (same version_id, same account fingerprint) before commit.
-- Re-sealing in place keeps every admitted attempt's credential binding valid. A refused refresh revokes the row:
-- only a new sign-in in Marketing OS can replace it. No key material is ever written to audit rows.

create or replace function public.cs_lock_attempt_credential(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_attempt_id uuid)
returns table (version_id uuid, provider_account_fingerprint text, ciphertext bytea, nonce bytea,
               encryption_key_version text)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare v public.cs_work_items; c cs_private.cs_credentials;
begin
  v := cs_private.cs_b3_lease(p_work_item_id, p_lease_token, p_fencing_token, 'provider_submit');
  select x.* into c
    from public.cs_attempts a join cs_private.cs_credentials x on x.version_id = a.credential_version_id
   where a.workspace_id = v.workspace_id and a.id = p_attempt_id and a.run_id = v.run_id and a.node_id = v.node_id
     and x.status = 'active' and x.mode = 'workspace'
     for update of x;
  if c.version_id is null then
    return;
  end if;
  return query select c.version_id, c.provider_account_fingerprint, c.ciphertext, c.nonce, c.encryption_key_version;
end $$;
revoke all on function public.cs_lock_attempt_credential(uuid, uuid, bigint, uuid)
  from public, anon, authenticated, cs_api, cs_worker;
grant execute on function public.cs_lock_attempt_credential(uuid, uuid, bigint, uuid) to cs_worker;

-- p_outcome: 'refreshed' (new ciphertext for the same login) | 'reconnect_required' (Higgsfield refused it).
create or replace function public.cs_settle_attempt_credential_refresh(
  p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint, p_attempt_id uuid, p_version_id uuid,
  p_outcome text, p_ciphertext bytea, p_nonce bytea, p_key_version text, p_runtime_principal text)
returns boolean
language plpgsql security definer set search_path = public, pg_temp as $$
declare v public.cs_work_items; n integer;
begin
  v := cs_private.cs_b3_lease(p_work_item_id, p_lease_token, p_fencing_token, 'provider_submit');
  if p_outcome not in ('refreshed', 'reconnect_required') then
    perform cs_private.cs_b3_refuse('invalid_refresh_outcome', null, 'CS422');
  end if;
  if not exists (select 1 from public.cs_attempts a
                  where a.workspace_id = v.workspace_id and a.id = p_attempt_id and a.run_id = v.run_id
                    and a.node_id = v.node_id and a.credential_version_id = p_version_id) then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  if p_outcome = 'refreshed' then
    update cs_private.cs_credentials c
       set ciphertext = p_ciphertext, nonce = p_nonce, encryption_key_version = p_key_version
     where c.version_id = p_version_id and c.workspace_id = v.workspace_id and c.mode = 'workspace'
       and c.status = 'active';
  else
    update cs_private.cs_credentials c set status = 'revoked', revoked_at = clock_timestamp()
     where c.version_id = p_version_id and c.workspace_id = v.workspace_id and c.mode = 'workspace'
       and c.status = 'active';
  end if;
  get diagnostics n = row_count;
  insert into public.cs_audit_events (account_id, workspace_id, runtime_principal, action, affected_refs)
  select w.account_id, v.workspace_id, p_runtime_principal,
         case when p_outcome = 'refreshed' then 'credential.refresh' else 'credential.reconnect_required' end,
         jsonb_build_array(jsonb_build_object('kind', 'credential_version', 'id', p_version_id))
    from public.cs_workspaces w where w.id = v.workspace_id and n > 0;
  return n > 0;
end $$;
revoke all on function public.cs_settle_attempt_credential_refresh(uuid, uuid, bigint, uuid, uuid, text, bytea, bytea, text, text)
  from public, anon, authenticated, cs_api, cs_worker;
grant execute on function public.cs_settle_attempt_credential_refresh(uuid, uuid, bigint, uuid, uuid, text, bytea, bytea, text, text)
  to cs_worker;
