-- Higgsfield credit balance read-back (2026-11-05, additive). Marketing OS shows and caps media spend in real Higgsfield
-- credits of the connected account. Creative Studio already reads the balance with the free `account status` check when a
-- login is stored, and the engine reads it before every paid create (and after, for a balance-delta charge). This keeps
-- the latest observed balance per workspace (never a guess: each row says where it came from and when) and exposes it,
-- with the current catalogue credit price, through the existing funding read. No new provider path, no key material.
-- Shared migration table with Marketing OS: CS slot ...29xxxx (VERSION_RANGES.md).

create table if not exists public.cs_credit_balances (
  workspace_id  uuid not null references public.cs_workspaces(id),
  provider      text not null check (provider ~ '^[a-z][a-z0-9_-]{0,79}$'),
  credits       numeric not null check (credits >= 0),
  source        text not null check (source in ('credential_check', 'run_balance_after', 'run_balance_before_less_charge',
                                                'run_balance_before')),
  observed_at   timestamptz not null default clock_timestamp(),
  primary key (workspace_id, provider)
);
alter table public.cs_credit_balances enable row level security;
alter table public.cs_credit_balances force row level security;
revoke all on table public.cs_credit_balances from public, anon, authenticated, cs_api, cs_worker;

create or replace function cs_private.cs_put_credit_balance(p_workspace_id uuid, p_provider text, p_credits numeric, p_source text)
returns void language sql set search_path = public, pg_temp as $$
  insert into public.cs_credit_balances (workspace_id, provider, credits, source, observed_at)
  values (p_workspace_id, p_provider, greatest(p_credits, 0), p_source, clock_timestamp())
  on conflict (workspace_id, provider) do update
    set credits = excluded.credits, source = excluded.source, observed_at = excluded.observed_at;
$$;

-- API (cs_api): the balance the free check returned when the owner's login was stored. Manage access, like storing it.
create or replace function public.cs_note_credential_balance(p_workspace_id uuid, p_provider text, p_credits numeric)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
begin
  perform cs_private.cs_require_actor();
  if not cs_private.cs_can(p_workspace_id, 'manage') then
    perform cs_private.cs_b3_refuse('capability_denied', null, 'CS403');
  end if;
  if p_credits is null or p_credits < 0 then perform cs_private.cs_b3_refuse('invalid_balance', null); end if;
  perform cs_private.cs_put_credit_balance(p_workspace_id, p_provider, p_credits, 'credential_check');
end $$;

-- Worker (cs_worker): what the engine observed around a paid create, bound to the live provider-submit lease.
create or replace function public.cs_record_credit_balance(p_work_item_id uuid, p_lease_token uuid, p_fencing_token bigint,
  p_provider text, p_credits numeric, p_source text)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare v public.cs_work_items;
begin
  v := cs_private.cs_b3_lease(p_work_item_id, p_lease_token, p_fencing_token, 'provider_submit');
  if p_credits is null or p_credits < 0 or p_source not in ('run_balance_after', 'run_balance_before_less_charge', 'run_balance_before') then
    perform cs_private.cs_b3_refuse('invalid_balance', null);
  end if;
  perform cs_private.cs_put_credit_balance(v.workspace_id, p_provider, p_credits, p_source);
end $$;

-- Read (manage access, like the budget read-back): latest observed balance + the current credit price per provider.
create or replace function public.cs_workspace_credit_balance(p_workspace_id uuid, p_provider text)
returns table(credits numeric, source text, observed_at timestamptz, credit_price_micros bigint)
language plpgsql stable security definer set search_path = public, pg_temp as $$
begin
  perform cs_private.cs_require_actor();
  if not cs_private.cs_can(p_workspace_id, 'read') then
    perform cs_private.cs_b3_refuse('resource_unavailable', null, 'CS404');
  end if;
  if not cs_private.cs_can(p_workspace_id, 'manage') then
    perform cs_private.cs_b3_refuse('capability_denied', null, 'CS403');
  end if;
  return query
    select b.credits, b.source, b.observed_at,
           (select max(c.credit_price_micros) from public.cs_provider_catalogue_versions c
             where c.provider = p_provider and c.enabled and c.valid_until > clock_timestamp())::bigint
      from (select 1) one
      left join public.cs_credit_balances b on b.workspace_id = p_workspace_id and b.provider = p_provider;
end $$;

revoke all on function cs_private.cs_put_credit_balance(uuid, text, numeric, text) from public, anon, authenticated, cs_api, cs_worker;
revoke all on function public.cs_note_credential_balance(uuid, text, numeric) from public, anon, authenticated;
revoke all on function public.cs_record_credit_balance(uuid, uuid, bigint, text, numeric, text) from public, anon, authenticated, cs_api;
revoke all on function public.cs_workspace_credit_balance(uuid, text) from public, anon, authenticated;
grant execute on function public.cs_note_credential_balance(uuid, text, numeric) to cs_api, service_role;
grant execute on function public.cs_record_credit_balance(uuid, uuid, bigint, text, numeric, text) to cs_worker;
grant execute on function public.cs_workspace_credit_balance(uuid, text) to cs_api, service_role;
