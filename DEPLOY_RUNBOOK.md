# Creative Studio Deployment Runbook (CS-B5)

This runbook provides mechanical, step-by-step commands for deploying the hosted Creative Studio service to production infrastructure (Supabase PostgreSQL + Storage and Vercel Serverless Functions).

---

## 1. Architecture Decision: Database Substrate

**Decision:** Deploy Creative Studio into the existing shared Supabase PostgreSQL project (`wwimngjmnuuitowujnif`, region `eu-north-1`) using dedicated schema (`cs_private`), table namespace (`cs_*`), and least-privilege roles (`cs_api`, `cs_worker`).

**Rationale:**
1. **Shared Identity Substrate:** Uses existing `auth.users` without cross-project federation overhead or token re-minting.
2. **Forced-RLS Isolation:** All `cs_*` tables enable and force Row Level Security (RLS). Under forced RLS only SELECT/INSERT are granted (to `authenticated`/runtime roles, and only where a policy admits the row); there is no UPDATE/DELETE/TRUNCATE for `anon`, `authenticated`, `cs_api` or `cs_worker`. State changes execute via audited `SECURITY DEFINER` procedures with fixed `search_path = public, pg_temp`.
3. **Co-location:** Co-located in `eu-north-1` with Marketing OS, reducing network roundtrip latency during handoffs and source verification.

---

## 2. Pre-Requisites & Required CLI Tools

Ensure you have authenticated CLIs:
- `supabase` CLI (v2.x+)
- `vercel` CLI (v38.x+)
- `psql` (PostgreSQL 17 compatible client)

---

## 3. Ordered Deployment Steps

### Step 1: Database Provisioning & Role Setup
Run the provisioning script as the database administrator (or Supabase CLI linked to `wwimngjmnuuitowujnif`):

```bash
# Set DB connection (Supabase admin session)
export PGDATABASE_ADMIN_URL="postgres://postgres.[ref]:[password]@aws-1-eu-north-1.pooler.supabase.com:5432/postgres"

# Apply Role and Schema Setup
psql "$PGDATABASE_ADMIN_URL" -f supabase/provisioning/01_roles_and_grants.sql

# Set strong passwords for the runtime roles:
# Generate random 32-character passwords for cs_api and cs_worker
CS_API_PASSWORD=$(openssl rand -hex 24)
CS_WORKER_PASSWORD=$(openssl rand -hex 24)

psql "$PGDATABASE_ADMIN_URL" -c "ALTER ROLE cs_api WITH LOGIN PASSWORD '$CS_API_PASSWORD';"
psql "$PGDATABASE_ADMIN_URL" -c "ALTER ROLE cs_worker WITH LOGIN PASSWORD '$CS_WORKER_PASSWORD';"
```

**Readback 1:** Verify roles:
```bash
psql "$PGDATABASE_ADMIN_URL" -c "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls FROM pg_roles WHERE rolname IN ('cs_api', 'cs_worker');"
# Expected: cs_api and cs_worker exist, rolcanlogin = t, rolsuper = f, rolbypassrls = f
```

**Rollback 1:**
```bash
psql "$PGDATABASE_ADMIN_URL" -c "ALTER ROLE cs_api NOLOGIN; ALTER ROLE cs_worker NOLOGIN;"
```

---

### Step 2: Apply Migrations in Order
Apply the four idempotent CS migrations in strict sequence:

```bash
psql "$PGDATABASE_ADMIN_URL" -f supabase/migrations/20261030210000_cs_b1_platform_foundation.sql
psql "$PGDATABASE_ADMIN_URL" -f supabase/migrations/20261031140100_cs_b2_brand_campaign_inputs.sql
psql "$PGDATABASE_ADMIN_URL" -f supabase/migrations/20261031140300_cs_b3_provider_execution.sql
psql "$PGDATABASE_ADMIN_URL" -f supabase/migrations/20261031140400_cs_b4_composition_exports.sql
```

**Readback 2:** Execute readback query:
```bash
psql "$PGDATABASE_ADMIN_URL" -f supabase/provisioning/04_readback_and_verification.sql
```

---

### Step 3: Storage Bucket Provisioning
Provision the private storage bucket and configure runtime limits:

```bash
psql "$PGDATABASE_ADMIN_URL" -f supabase/provisioning/02_storage_bucket.sql
```

**Readback 3:** Verify bucket:
```bash
psql "$PGDATABASE_ADMIN_URL" -c "SELECT id, name, public, file_size_limit FROM storage.buckets WHERE id = 'creative-studio';"
# Expected: 1 row with public = f, file_size_limit = 50000000
```

---

### Step 3b: Worker Identity (storage credential)
The worker authenticates to Storage as a dedicated Auth user (password grant, token cached and refreshed in-process);
there is no static token. Create it, register it as a runtime principal and read back the RLS decision. Full detail,
the documented gap and revoke/rotate: `supabase/provisioning/05_worker_identity.md`.

```bash
export CS_ADMIN_AUTH_URL="https://wwimngjmnuuitowujnif.supabase.co/auth/v1"
export CS_ADMIN_SERVICE_KEY=...                       # used only by this script; never stored in Vercel
export CS_STORAGE_WORKER_EMAIL="cs-worker-production@<your-domain>"
export CS_STORAGE_WORKER_PASSWORD="$(openssl rand -hex 32)"
export CS_ENVIRONMENT_ID=production
python3 supabase/provisioning/05_worker_identity.py
# Expected: worker user id + six [PASS] lines, exit 0 (worker permitted, unrelated user denied)
```

---

### Step 4: Create Vercel Project & Configure Environment
Link to Vercel team `craigs-projects-a8e1637a` (`team_n1rMtzqnARnlXX4LEA8eYlKR`):

```bash
# Link or create project
vercel link --project creative-studio --scope team_n1rMtzqnARnlXX4LEA8eYlKR --yes

# Set Environment Variables. PRODUCTION ONLY: preview deploys get none of these and will refuse to
# start (config fails closed) - that is expected; do not copy secrets into the preview scope.
vercel env add CS_ENVIRONMENT_ID production production
vercel env add CS_RELEASE_ID $(git rev-parse HEAD) production
vercel env add CS_API_DATABASE_URL "postgres://cs_api:$CS_API_PASSWORD@aws-1-eu-north-1.pooler.supabase.com:6543/postgres?sslmode=require" production
vercel env add CS_WORKER_DATABASE_URL "postgres://cs_worker:$CS_WORKER_PASSWORD@aws-1-eu-north-1.pooler.supabase.com:6543/postgres?sslmode=require" production
vercel env add CS_JWT_ISSUER "https://wwimngjmnuuitowujnif.supabase.co/auth/v1" production
vercel env add CS_JWT_AUDIENCE "authenticated" production
vercel env add CS_STORAGE_URL "https://wwimngjmnuuitowujnif.supabase.co/storage/v1" production
vercel env add CS_STORAGE_ANON_KEY "<public anon key>" production
vercel env add CS_AUTH_URL "https://wwimngjmnuuitowujnif.supabase.co/auth/v1" production
# Worker identity from Step 3b (password grant; no static JWT env exists)
vercel env add CS_STORAGE_WORKER_EMAIL "$CS_STORAGE_WORKER_EMAIL" production
vercel env add CS_STORAGE_WORKER_PASSWORD "$CS_STORAGE_WORKER_PASSWORD" production

# Worker Secret: bearer for /api/internal/worker. vercel.json has exactly ONE daily cron (Hobby plan: safety net,
# GET with Authorization: Bearer $CRON_SECRET); real-time starts come from the Postgres kick/pg_net job (ENG-12).
# Set CRON_SECRET to the SAME value as CS_WORKER_SECRET so the daily cron authenticates.
CS_WORKER_SECRET=$(openssl rand -hex 32)
vercel env add CS_WORKER_SECRET "$CS_WORKER_SECRET" production
vercel env add CRON_SECRET "$CS_WORKER_SECRET" production

# Credential Encryption Key (AES-256-GCM 32-byte Base64)
CS_ENCRYPTION_KEY=$(openssl rand -base64 32)
vercel env add CS_CREDENTIAL_ENCRYPTION_KEY_V1 "$CS_ENCRYPTION_KEY" production

# Marketing OS Gateway URL and token
vercel env add CS_MOS_GATEWAY_URL "https://marketing-os.vercel.app" production
```

**Readback 4:** Verify env list:
```bash
vercel env ls
```

---

### Step 5: Deploy to Production & Smoke Test
Env vars exist only in the production scope (Step 4), so a preview deploy cannot start and proves nothing about
readiness. Deploy to production and verify there:

```bash
vercel deploy --prod --yes
PROD_URL="https://creative-studio.vercel.app"
curl -f -s "$PROD_URL/api/v1/health" | jq .
curl -f -s "$PROD_URL/api/v1/readiness" | jq .
# Expected readiness (HTTP 200 only after a real worker sign-in AND an authorised Storage probe):
# {
#   "status": "ready",
#   "release_id": "...",
#   "database_role": "cs_api",
#   "renderer_version": "cs-image-compositor/1 pillow/12.2.0 ...",
#   "storage": "configured"
# }
# A red reason is typed: worker_identity_unconfigured | worker_signin_failed | worker_auth_unavailable |
# storage_credential_rejected | storage_unavailable. The response never contains the credential.
```

---

## 4. Emergency Rollback

If issues occur post-deployment:
1. **Vercel Rollback:**
   ```bash
   vercel rollback [PREVIOUS_DEPLOYMENT_ID] --yes
   ```
2. **Database Rollback:**
   Migrations are additive and backward-compatible. Disable dispatch or admission immediately if needed:
   ```bash
   psql "$PGDATABASE_ADMIN_URL" -c "UPDATE cs_private.cs_runtime_limits SET dispatch_enabled = false, admission_enabled = false WHERE environment_id = 'production';"
   ```

## 5. Use-time kick (CS-12) — operator steps, in this order, AFTER the first manual end-to-end run works

Work now starts when it is enqueued: a trigger on `cs_work_items` fires one async `net.http_post` to
`<worker_base_url>/api/internal/worker`. The scheduler is only a safety net. Nothing below was applied by CS-12.

1. Apply `supabase/migrations/20261031190100_cs_12_use_time_kick.sql` (also listed in `03_apply_all_migrations.sql`). Kicking stays OFF by default.
2. Vault secret (same value as the Vercel `CS_WORKER_SECRET`): `select vault.create_secret('<CS_WORKER_SECRET>', 'cs_worker_cron_secret');`
3. Settings: `insert into cs_private.cs_platform_settings(key,value) values ('worker_base_url','https://<deployment host>') on conflict (key) do update set value=excluded.value;`
   Tunables (all in the same table): `worker_kick_window_seconds` (2), `worker_safety_net_seconds` (120), `worker_sweep_grace_seconds` (20).
4. Turn on: `update cs_private.cs_platform_settings set value='true' where key='worker_kick_enabled';`
   Turn off (kill switch, instant): same with `'false'`.
5. Run `scripts/ops/sql/cs_worker_dispatch.sql` to (re)schedule `cs-worker-drain` as a 2-minute safety net (pause: `select cron.unschedule('cs-worker-drain');`).
6. Watch: `select * from cs_private.cs_kick_failures order by id desc limit 20;` (pg_net non-2xx answers are recorded by the sweep).

Known limits: a retry scheduled for the future (`retry_wait` with a later `due_at`) cannot be kicked at its due time
(pg_net has no delay) — the 2-minute safety net starts it. A burst is coalesced to one kick per workspace per window; the
remaining items are chained (each finished item kicks the next) or picked up by the safety net.

**Vercel cron (Hobby):** `vercel.json` keeps ONE daily safety-net cron (`17 3 * * *` on `/api/internal/worker`; the
endpoint accepts the cron's bearer GET). There is no every-minute cron. The pg_cron `cs-worker-drain` cadence is a config
value, `cs_platform_settings.worker_safety_net_seconds` (default 120 = 2 minutes); change it and re-run the script.

**Spend switch (CS-INT2):** Creative Studio is on-demand. Preparation is free; provider spend needs an explicit
admission (the user's Produce click) AND `cs_private.cs_runtime_limits.paid_provider_enabled = true`.
`02_storage_bucket.sql` seeds `admission_enabled = true, dispatch_enabled = true, paid_provider_enabled = false`;
`paid_provider_enabled` stays false until the operator's first Produce, then:
`update cs_private.cs_runtime_limits set paid_provider_enabled = true where environment_id = 'production';`
While false, the kick may run free stages but `cs_claim_work` never leases `provider_submit`, and a DB trigger refuses
attempt creation/dispatch with `paid_provider_disabled`. Admitted runs wait (queued, funds reserved) and proceed when
the switch is turned on. Apply migrations only in chain order (a standalone B2 re-run restores B2's `cs_claim_work`; re-apply
`20261031190300` after it).

**Worker identity registration:** `05_worker_identity.py` registers the principal through
`cs_private.cs_register_runtime_principal(uuid, text)` (migration `20261031190200`, SECURITY DEFINER, owner/admin DSN
only; refused for anon, authenticated, cs_api, cs_worker and service_role).
