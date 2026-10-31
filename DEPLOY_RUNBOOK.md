# Creative Studio Deployment Runbook (CS-B5)

This runbook provides mechanical, step-by-step commands for deploying the hosted Creative Studio service to production infrastructure (Supabase PostgreSQL + Storage and Vercel Serverless Functions).

---

## 1. Architecture Decision: Database Substrate

**Decision:** Deploy Creative Studio into the existing shared Supabase PostgreSQL project (`wwimngjmnuuitowujnif`, region `eu-north-1`) using dedicated schema (`cs_private`), table namespace (`cs_*`), and least-privilege roles (`cs_api`, `cs_worker`).

**Rationale:**
1. **Shared Identity Substrate:** Uses existing `auth.users` without cross-project federation overhead or token re-minting.
2. **Zero-Grant Isolation:** All `cs_*` tables enable and force Row Level Security (RLS). No direct table permissions are granted to `anon`, `authenticated`, `cs_api`, or `cs_worker`. All operations execute strictly via audited `SECURITY DEFINER` procedures with fixed `search_path = public, pg_temp`.
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
export PGDATABASE_ADMIN_URL="postgres://postgres.[ref]:[password]@aws-0-eu-north-1.pooler.supabase.com:5432/postgres"

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

### Step 3: Storage Bucket & Worker Identity Provisioning
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

### Step 4: Create Vercel Project & Configure Environment
Link to Vercel team `craigs-projects-a8e1637a` (`team_n1rMtzqnARnlXX4LEA8eYlKR`):

```bash
# Link or create project
vercel link --project creative-studio --scope team_n1rMtzqnARnlXX4LEA8eYlKR --yes

# Set Environment Variables (Production & Preview)
vercel env add CS_ENVIRONMENT_ID production production
vercel env add CS_RELEASE_ID $(git rev-parse HEAD) production
vercel env add CS_API_DATABASE_URL "postgres://cs_api:$CS_API_PASSWORD@aws-0-eu-north-1.pooler.supabase.com:6543/postgres?sslmode=require" production
vercel env add CS_WORKER_DATABASE_URL "postgres://cs_worker:$CS_WORKER_PASSWORD@aws-0-eu-north-1.pooler.supabase.com:6543/postgres?sslmode=require" production
vercel env add CS_JWT_ISSUER "https://wwimngjmnuuitowujnif.supabase.co/auth/v1" production
vercel env add CS_JWT_AUDIENCE "authenticated" production
vercel env add CS_STORAGE_URL "https://wwimngjmnuuitowujnif.supabase.co/storage/v1" production

# Worker Secret for cron authentication
CS_WORKER_SECRET=$(openssl rand -hex 32)
vercel env add CS_WORKER_SECRET "$CS_WORKER_SECRET" production

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

### Step 5: Deploy Preview & Smoke Test

Deploy preview build to Vercel:
```bash
PREVIEW_URL=$(vercel deploy --yes)
echo "Preview URL: $PREVIEW_URL"
```

**Smoke Test Readiness Endpoint:**
```bash
curl -f -s "$PREVIEW_URL/api/v1/readiness" | jq .
# Expected Output:
# {
#   "status": "ready",
#   "release_id": "...",
#   "database_role": "cs_api",
#   "renderer_version": "cs-image-compositor/1 pillow/12.2.0 ...",
#   "storage": "configured"
# }

# Verify health endpoint
curl -f -s "$PREVIEW_URL/api/v1/health" | jq .
```

---

### Step 6: Promote to Production

Once preview tests pass:
```bash
vercel deploy --prod --yes
```

**Verify Production Service:**
```bash
PROD_URL="https://creative-studio.vercel.app"
curl -f -s "$PROD_URL/api/v1/readiness" | jq .
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

**Follow-up (Vercel Hobby):** once the kick path is live, the `crons` block in `vercel.json` (`* * * * *` on
`/api/internal/worker`) is no longer needed and Hobby's once-per-day cron limit becomes irrelevant. Remove the block in a
separate change after step 5 has run clean for a few days; CS-12 deliberately left `vercel.json` untouched.
