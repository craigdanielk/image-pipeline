# Migration version ranges (shared `supabase_migrations.schema_migrations`)

Marketing OS and Creative Studio share ONE `supabase_migrations.schema_migrations` table; `version` is its primary key,
so two files with the same version collide at apply time.

- Creative Studio versions for 2026-10-31 onwards live in `20261031240000` and above (`…24xxxx`+).
- Marketing OS used `…23xxxx` and below on 2026-10-31.
- Before choosing a version, run `select version from supabase_migrations.schema_migrations` and list the other
  repo's `supabase/migrations`. Never assume a slot is free.

## Changelog
- CS-17d: `20261031230100_cs_17b_post_run_pipeline.sql` renamed to `20261031240100_cs_17b_post_run_pipeline.sql`
  (the old version was already live as Marketing OS `mos_awaiting_user_and_spend_switch`).
