# 05 — Storage worker identity (CS-13)

The worker writes/reads Storage as a **dedicated Supabase Auth user**, not a static token. `service/storage.py`
signs this user in with the Auth password grant, caches the access token until `exp − 120 s`, refreshes it
thread-safely, and on a 401 refreshes once and retries once. There is **no** static JWT env var
(`CS_STORAGE_WORKER_CREDENTIAL` is removed). Hosted mode fails closed (`worker_identity_unconfigured`) without
the email/password, and `/api/v1/readiness` reports `storage: configured` only after a real sign-in and an
authorised Storage probe.

## Gap this closes
Nothing in migrations `20261030210000`…`20261031140400` creates the auth user or inserts the
`cs_private.cs_runtime_principals` row, and no SECURITY DEFINER function exists to register a principal.
The script therefore writes that row from the admin session (the only writer, because the table has forced RLS and
no grants). If a registration function is wanted, that is a new migration — out of scope here.

## Run (operator, once per environment; idempotent)
```bash
export PGDATABASE_ADMIN_URL=...        # admin DSN (RLS-bypassing role); pooler host aws-1-eu-north-1
export CS_ADMIN_AUTH_URL="https://wwimngjmnuuitowujnif.supabase.co/auth/v1"
export CS_ADMIN_SERVICE_KEY=...        # service key; used only by this script; never put in Vercel
export CS_STORAGE_WORKER_EMAIL="cs-worker-production@<your-domain>"
export CS_STORAGE_WORKER_PASSWORD="$(openssl rand -hex 32)"   # keep: it goes into the worker env
export CS_ENVIRONMENT_ID=production
python3 supabase/provisioning/05_worker_identity.py
```
Prerequisites: migrations applied (Step 2) and `02_storage_bucket.sql` run (it creates the `cs_runtime_limits` row the
principal references). The script prints only the worker user id and PASS/FAIL lines — never the key or password.

## What it verifies (exit 0 only if all pass)
1. `cs_storage_allowed` **permits** the worker: `select` and `insert` under `<environment_id>/…`.
2. **Denies** the worker outside its prefix and for `delete`.
3. **Denies** an unrelated authenticated user (no principal row, grant or intent) for `select` and `insert`.

## Rotate / revoke
- Rotate: re-run with a new `CS_STORAGE_WORKER_PASSWORD`, update the Vercel production env, redeploy.
- Revoke: `update cs_private.cs_runtime_principals set status = 'revoked' where user_id = '<id>';` (immediate).
