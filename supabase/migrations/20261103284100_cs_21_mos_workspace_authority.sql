-- CS-21: Marketing OS is the authority for a workspace bound to it.
--
-- A Marketing OS workspace has exactly one bound Creative Studio workspace (cs_external_bindings_external_key), and
-- it lives in whichever Studio account created it. Other Marketing OS members (a producer connecting their own
-- Higgsfield account, producing media) are not members of that Studio account, so every call they made was refused.
--
-- Marketing OS checks the member's current workspace role before it signs a request, and the API verifies that
-- signature, its route, body hash, nonce and the active binding. For that one verified request the API sets
-- `cs.mos_grant = '<user_id>:<workspace_id>'` (transaction-local; only the API's own session can set it), and the
-- role check admits that user on that bound workspace only. A user who is already an account or workspace member keeps
-- their Studio role (the grant admits newcomers; it never widens a member's role).
--
-- * cs_private.cs_role_allows: + verified Marketing OS grant on an actively bound workspace
-- * public.cs_resolve_marketing_os_binding(external): the bound Studio workspace for a Marketing OS workspace, so
--   Marketing OS finds the one link whoever created it (cs_api only; the route requires a signed assertion).

create or replace function cs_private.cs_role_allows(p_user_id uuid, p_workspace_id uuid, p_action text)
returns boolean language sql stable security definer set search_path = public, pg_temp as $$
  select p_user_id is not null and p_action in ('read', 'download', 'review', 'produce', 'manage') and (
    exists (
      select 1
        from public.cs_workspaces w
        join public.cs_accounts a on a.id = w.account_id and a.status = 'active'
        join public.cs_account_members am on am.account_id = w.account_id
       where w.id = p_workspace_id and w.status = 'active'
         and am.user_id = p_user_id and am.revoked_at is null)
    or exists (
      select 1
        from public.cs_workspaces w
        join public.cs_accounts a on a.id = w.account_id and a.status = 'active'
        join public.cs_workspace_members m on m.workspace_id = w.id
       where w.id = p_workspace_id and w.status = 'active' and w.mode = 'native'
         and m.user_id = p_user_id and m.revoked_at is null
         and case p_action
               when 'read'     then true
               when 'download' then m.role in ('producer', 'reviewer')
               when 'review'   then m.role = 'reviewer'
               when 'produce'  then m.role = 'producer'
               else false
             end)
    or (
      coalesce(current_setting('cs.mos_grant', true), '') = p_user_id::text || ':' || p_workspace_id::text
      and exists (
        select 1
          from public.cs_workspaces w
          join public.cs_accounts a on a.id = w.account_id and a.status = 'active'
          join public.cs_external_bindings b on b.workspace_id = w.id
         where w.id = p_workspace_id and w.status = 'active'
           and b.authority = 'marketing-os' and b.status = 'active')
      -- A user the Studio already knows on this workspace keeps their Studio role; the grant never widens it.
      and not exists (
        select 1 from public.cs_workspaces w
          join public.cs_account_members am on am.account_id = w.account_id
         where w.id = p_workspace_id and am.user_id = p_user_id and am.revoked_at is null)
      and not exists (
        select 1 from public.cs_workspace_members m
         where m.workspace_id = p_workspace_id and m.user_id = p_user_id and m.revoked_at is null)));
$$;

create or replace function public.cs_resolve_marketing_os_binding(p_external_workspace_id text)
returns table (workspace_id uuid, account_id uuid, connection_id uuid)
language sql stable security definer set search_path = public, pg_temp as $$
  select w.id, w.account_id, b.id
    from public.cs_external_bindings b
    join public.cs_workspaces w on w.id = b.workspace_id and w.status = 'active'
    join public.cs_accounts a on a.id = w.account_id and a.status = 'active'
   where b.authority = 'marketing-os' and b.status = 'active' and b.external_workspace_id = p_external_workspace_id
   limit 1;
$$;

revoke all on function public.cs_resolve_marketing_os_binding(text) from public, anon, authenticated, cs_worker;
grant execute on function public.cs_resolve_marketing_os_binding(text) to cs_api;
