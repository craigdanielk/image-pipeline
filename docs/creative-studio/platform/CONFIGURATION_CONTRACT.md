# Creative Studio Configuration Contract (CS-B5)

This contract defines every environment variable, secret, and configuration setting required by the hosted Creative Studio service.

## Core Security Policy: No Provider Keys in Environment
In accordance with the operator architectural decision (DECISION record amendment 6) and the Marketing OS security model:
- **NO provider API keys (OpenAI, Gemini, Higgsfield, Anthropic, etc.) may be placed in environment variables or deployment configuration.**
- All provider credentials are provided per-workspace or platform-level via encrypted database records (`cs_private.cs_credentials`), managed through the operator console UI.
- The deployment environment contains **only infrastructure identity**: database connection strings for scoped roles, JWT verification keys, private storage credentials, and service tokens.

---

## Configuration Registry

| Variable Name | Purpose | Source | Set By | Consumers | Secret? |
|---|---|---|---|---|---|
| `CS_ENVIRONMENT_ID` | Identifies execution environment (`production`, `staging`, `test`). Must match `^[a-z0-9][a-z0-9_-]{0,39}$`. | Vercel Env | Operator / CI | API, Worker | No |
| `CS_RELEASE_ID` | Deployment release identifier (git commit hash or semver). | Vercel Env / Git | CI Build / Vercel | API, Worker | No |
| `CS_API_DATABASE_URL` | PostgreSQL connection pooler DSN for the `cs_api` role. Connects via transaction pooler with TLS. | Supabase / Vault | Operator | API | **Yes** |
| `CS_WORKER_DATABASE_URL` | PostgreSQL connection pooler DSN for the `cs_worker` role. Has worker procedure execute grants only. | Supabase / Vault | Operator | Worker | **Yes** |
| `CS_JWT_ISSUER` | Supabase Auth JWT issuer URL (e.g. `https://<ref>.supabase.co/auth/v1`). | Supabase | Operator | API | No |
| `CS_JWT_AUDIENCE` | Expected JWT audience claim (defaults to `authenticated`). | Vercel Env | Operator | API | No |
| `CS_JWT_SECRET` | HS256 secret for verifying user JWTs (if symmetric signing is used). | Supabase / Vault | Operator | API | **Yes** |
| `CS_JWKS_URL` | Asymmetric JWKS URL for verifying tokens (alternative to `CS_JWT_SECRET`). | Supabase | Operator | API | No |
| `CS_STORAGE_URL` | Supabase Storage v1 endpoint (`https://<ref>.supabase.co/storage/v1`). | Supabase | Operator | API, Worker | No |
| `CS_STORAGE_ANON_KEY` | Public Supabase anon key required by Storage alongside user bearer tokens. | Supabase | Operator | API, Worker | No |
| `CS_STORAGE_WORKER_ID` | UUID of the dedicated Auth principal representing the storage worker. | Supabase Auth | Operator | Worker | No |
| `CS_STORAGE_WORKER_CREDENTIAL` | Storage authentication token for the worker principal (scoped to `creative-studio` bucket). | Supabase Auth | Operator | Worker | **Yes** |
| `CS_WORKER_SECRET` | Shared bearer secret required by `/api/internal/worker` endpoint. Scheduled cron passes this in `Authorization: Bearer <token>`. | Vercel Env / Supabase Vault | Operator | Worker, Scheduler | **Yes** |
| `CS_CREDENTIAL_ENCRYPTION_KEY_V1` | 32-byte Base64-encoded AES-256-GCM key used to seal and unseal provider credentials in `cs_private.cs_credentials`. | Vault / Vercel Env | Operator | Worker | **Yes** |
| `CS_CREDENTIAL_KEYS` | JSON map of `{version: base64_key}` for key rotation (optional alternative to `CS_CREDENTIAL_ENCRYPTION_KEY_V1`). | Vault / Vercel Env | Operator | Worker | **Yes** |
| `CS_MOS_GATEWAY_URL` | Base origin of the Marketing OS host (e.g. `https://marketing-os.vercel.app`). | Platform UI / Vercel Env | Operator | API, Worker | No |
| `CS_MOS_SERVICE_TOKEN` | Service authentication bearer token for communicating with Marketing OS gateway. | Vault / Vercel Env | Operator | API, Worker | **Yes** |
| `CS_ALLOWED_ORIGINS` | Comma-separated list of allowed CORS web origins for browser UI. | Vercel Env | Operator | API | No |
| `CS_SIGNED_DOWNLOAD_MAX_SECONDS` | Maximum TTL in seconds for signed storage download URLs (default 300, max 300). | Vercel Env | Operator (default) | API | No |
| `CS_DOWNLOAD_GRANT_MAX_SECONDS` | Maximum TTL in seconds for database download grants (default 60, max 60). | Vercel Env | Operator (default) | API | No |

---

## Forbidden Environment Secrets
The following must **NEVER** appear in the runtime environment:
1. `SUPABASE_SERVICE_ROLE_KEY`: bypasses RLS and violates least-privilege architecture.
2. `OPENAI_API_KEY`, `GEMINI_API_KEY`, `HIGGSFIELD_API_KEY`: provider keys must only be loaded from sealed workspace/platform credential records.
3. Local developer CLI tokens or session files.
