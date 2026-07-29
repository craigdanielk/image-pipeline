-- Auth user profiles — wire Supabase Auth users to workspace access.
--
-- Every authenticated user automatically gets a row here via the DB trigger
-- below. The UI uses this table to gate tenant/workspace switching and to
-- display user info in the topbar.
--
-- This migration is additive (safe to ship with existing data). The
-- user_profiles table is consumed by the SPA's AuthContext (which reads the
-- session JWT, not this table directly) and optionally by the server for
-- RBAC checks.

-- ============================================================
-- 1. user_profiles — one row per auth.uid
-- ============================================================
create table if not exists user_profiles (
  id                uuid primary key references auth.users(id) on delete cascade,
  email             text,
  display_name      text,
  avatar_url        text,
  default_workspace_id uuid references workspaces(id) on delete set null,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

-- Auto-create a profile row when a new user signs up via Supabase Auth.
create or replace function handle_new_user()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
begin
  insert into public.user_profiles (id, email, display_name)
  values (
    new.id,
    new.email,
    coalesce(new.raw_user_meta_data ->> 'display_name', split_part(new.email, '@', 1))
  );
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function handle_new_user();

-- touch trigger
create or replace function public.set_updated_at() returns trigger as $$
begin new.updated_at = now(); return new; end;
$$ language plpgsql;

drop trigger if exists trg_user_profiles_touch on user_profiles;
create trigger trg_user_profiles_touch
  before update on user_profiles
  for each row execute function public.set_updated_at();

-- Enable Row-Level Security so user_profiles data stays safe.
alter table user_profiles enable row level security;

-- Users can read any profile (for presence/display) but only update their own.
create policy "Anyone can view profiles"
  on user_profiles for select
  using (true);

create policy "Users can update own profile"
  on user_profiles for update
  using (auth.uid() = id)
  with check (auth.uid() = id);
