-- =============================================================================
-- Creative Studio Provisioning: 01_roles_and_grants.sql
-- Role creation for cs_api and cs_worker with least privilege.
-- Idempotent, safe to run multiple times.
-- =============================================================================

do $$
begin
  -- 1. Create cs_api role if not exists
  if to_regrole('cs_api') is null then
    create role cs_api nologin noinherit;
  end if;

  -- 2. Create cs_worker role if not exists
  if to_regrole('cs_worker') is null then
    create role cs_worker nologin noinherit;
  end if;
end $$;

-- Enforce security constraints on roles: never superuser, createrole, createdb, or bypassrls
alter role cs_api nosuperuser nocreatedb nocreaterole nobypassrls;
alter role cs_worker nosuperuser nocreatedb nocreaterole nobypassrls;

-- Schema usage: cs_api and cs_worker need usage on public and cs_private schemas
grant usage on schema public to cs_api, cs_worker;
grant usage on schema cs_private to cs_api, cs_worker;

-- Revoke create on schemas
revoke create on schema public, cs_private from cs_api, cs_worker;

-- Table and procedure privileges are defined and enforced per migration
-- with strict RLS policies (B1-B3) or procedure-only SECURITY DEFINER access (B4).
