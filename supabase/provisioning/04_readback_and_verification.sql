-- =============================================================================
-- Creative Studio Provisioning: 04_readback_and_verification.sql
-- Readback verification queries proving roles, RLS enforcement, least privilege,
-- and storage state post-apply.
-- =============================================================================

-- 1. Prove roles cs_api and cs_worker exist and have NO bypassrls or superuser
select
  rolname,
  rolsuper,
  rolinherit,
  rolcreaterole,
  rolcreatedb,
  rolcanlogin,
  rolbypassrls
from pg_roles
where rolname in ('cs_api', 'cs_worker')
order by rolname;

-- 2. List direct table privileges on cs_* tables. Expected under forced RLS: only SELECT/INSERT, and only where a
--    policy admits the row; no UPDATE/DELETE/TRUNCATE for anon, authenticated, cs_api or cs_worker.
select
  table_schema,
  table_name,
  grantee,
  privilege_type
from information_schema.role_table_grants
where grantee in ('cs_api', 'cs_worker', 'anon', 'authenticated')
  and table_name like 'cs_%'
order by table_schema, table_name, grantee;

-- 3. Prove RLS is enabled and forced on all cs_* tables (public and cs_private)
select
  n.nspname as schema_name,
  c.relname as table_name,
  c.relrowsecurity as rls_enabled,
  c.relforcerowsecurity as rls_forced
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname in ('public', 'cs_private') and c.relkind in ('r', 'p') and c.relname like 'cs_%'
order by n.nspname, c.relname;

-- 4. Prove security definer search_path isolation on procedures
select
  n.nspname as schema_name,
  p.proname as function_name,
  p.prosecdef as is_security_definer,
  p.proconfig as config_settings
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname in ('public', 'cs_private')
  and p.proname like 'cs_%'
  and p.prosecdef = true
order by n.nspname, p.proname;

-- 5. Prove private storage bucket exists and is not public
select
  id,
  name,
  public,
  file_size_limit,
  allowed_mime_types
from storage.buckets
where id = 'creative-studio';

-- 6. Prove runtime limits row exists
select
  environment_id,
  admission_enabled,
  dispatch_enabled,
  paid_provider_enabled,
  heavy_slots,
  active_run_limit,
  max_dispatch_batch
from cs_private.cs_runtime_limits;
