-- CS-INT2: sanctioned path to register the storage worker identity (replaces a raw admin-session insert).
-- SECURITY DEFINER, fixed search_path. Executable only by the owner/admin session (provisioning DSN);
-- never by anon, authenticated, cs_api or cs_worker. Idempotent (upsert, re-activates).
create or replace function cs_private.cs_register_runtime_principal(p_user_id uuid, p_environment_id text)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if p_user_id is null or not exists (select 1 from auth.users u where u.id = p_user_id) then
    raise exception 'principal_user_unknown' using errcode = 'CS422';
  end if;
  if p_environment_id is null
     or not exists (select 1 from cs_private.cs_runtime_limits l where l.environment_id = p_environment_id) then
    raise exception 'environment_unavailable' using errcode = 'CS503';
  end if;
  insert into cs_private.cs_runtime_principals (user_id, environment_id, role, status)
  values (p_user_id, p_environment_id, 'storage_worker', 'active')
  on conflict (user_id, environment_id) do update set status = 'active';
end $$;

revoke all on function cs_private.cs_register_runtime_principal(uuid, text)
  from public, anon, authenticated, cs_api, cs_worker;
-- No grant to service_role either: it has no USAGE on schema cs_private (deliberately). The provisioning
-- script connects with the admin DSN (function owner), which is the only sanctioned caller.
