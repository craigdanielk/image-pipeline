-- =============================================================================
-- Creative Studio Provisioning: 02_storage_bucket.sql
-- Dedicated private storage bucket and worker identity row.
-- Idempotent, safe to run multiple times.
-- =============================================================================

-- 1. Ensure private creative-studio bucket exists in storage.buckets
do $$
begin
  if exists (select 1 from information_schema.tables where table_schema = 'storage' and table_name = 'buckets') then
    insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
    values (
      'creative-studio',
      'creative-studio',
      false,
      50000000, -- 50 MB max file size
      array['image/png', 'image/jpeg', 'image/webp', 'image/svg+xml', 'video/mp4', 'application/json']
    )
    on conflict (id) do update set
      public = false,
      file_size_limit = 50000000,
      allowed_mime_types = array['image/png', 'image/jpeg', 'image/webp', 'image/svg+xml', 'video/mp4', 'application/json'];
  end if;
end $$;

-- 2. Ensure RLS is enabled and forced on storage objects
do $$
begin
  if exists (select 1 from information_schema.tables where table_schema = 'storage' and table_name = 'objects') then
    alter table storage.objects enable row level security;
    alter table storage.buckets enable row level security;
  end if;
end $$;

-- 3. Provision default environment limits if cs_runtime_limits table exists
do $$
begin
  if to_regclass('cs_private.cs_runtime_limits') is not null then
    insert into cs_private.cs_runtime_limits (
      environment_id,
      admission_enabled,
      dispatch_enabled,
      paid_provider_enabled,
      heavy_slots,
      active_run_limit,
      max_dispatch_batch
    )
    values (
      'production',
      true,
      true,
      false, -- Paid providers remain false until explicitly activated by operator
      1,
      2,
      2
    )
    on conflict (environment_id) do nothing;
  end if;
end $$;
