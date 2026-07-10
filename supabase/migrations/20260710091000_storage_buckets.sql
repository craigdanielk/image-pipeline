-- image-pipeline cloud replatform — Supabase Storage buckets
-- Brief #30201. Replaces disk directories:
--   reference-images  <- was: jobs/**/reference/ and jobs/**/refs/ (input assets)
--   rendered-outputs  <- was: jobs/**/out/*.png (engine renders)
--
-- Both buckets are PRIVATE. The UI never exposes a raw object URL or a disk
-- path; ui/server.py mints a short-lived signed URL per request (see
-- cloud_store.signed_url / GET /media?path=). This is what replaces the old
-- FileResponse-from-disk /media handler.

insert into storage.buckets (id, name, public)
values
  ('reference-images', 'reference-images', false),
  ('rendered-outputs', 'rendered-outputs', false)
on conflict (id) do nothing;

-- Service-role access only for now (single-operator, matches db/SCHEMA.md
-- decision #1: no RLS on data tables until this becomes shared SaaS). The
-- service key used by ui/server.py bypasses storage RLS, so no per-object
-- policy is required to read/write. When public multi-tenant lands, add
-- per-workspace policies keyed on the object path prefix (<workspace>/...).
