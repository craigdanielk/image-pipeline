# Creative Studio hosted service — CS-B1 platform foundation (NS-10803)

| Entrypoint | Module | Role |
|---|---|---|
| `api/index.py` | `service/api.py` | Versioned `/api/v1` API on the restricted `cs_api` login |
| `api/worker.py` | `service/worker.py` | One leased work item per authenticated POST on the `cs_worker` login |

Schema: `supabase/migrations/20261030210000_cs_b1_platform_foundation.sql`.

- **Identity:** a Supabase access token verified for signature, issuer, audience, expiry and `role=authenticated`. Only `sub` reaches the database (`request.jwt.claims`, transaction-local). There is no actor parameter anywhere.
- **Authorization:** in SQL, on every statement, from current `cs_account_members` / `cs_workspace_members` rows. Workspace roles: producer, reviewer, viewer. Account owner/admin manages. RLS is forced on every `cs_*` table, and browser roles (`anon`, `authenticated`) have no `cs_*` table or procedure grants.
- **Private storage:** one private `creative-studio` bucket with keys `{env}/{workspace}/{asset}/{version}/{sha256}.{ext}`. Uploads go through an exact-key intent; downloads use a ≤60 s exact-object grant plus a ≤300 s signed URL minted by Storage *as the user*. Storage URLs from any other origin are refused.
- **Durable records:** runs, run nodes, append-only run events, work items, stage results (receipts), assets, immutable asset versions, idempotency receipts and audit events.
- **Scheduling primitive:** `cs_claim_work` (SKIP LOCKED, fencing token, hard 360 s lease), `cs_heartbeat_work`, `cs_finish_work`, `cs_reschedule_work` (bounded attempts → dead). An expired paid-stage lease is not re-claimed.
- **Hosted vs local:** there is no `jobs_root`, tenant slug, local path or `/media?path` surface; unknown query parameters are refused. `ui/server.py` remains the local operator entrypoint.

Out of scope for B1 (not faked): plans/recipes (B2), admission, stage handlers, providers and dispatcher cron (B3), compositions/exports (B4), deployment and runtime login credentials (B5).

Dependencies: `service/requirements.txt` is the hosted runtime. The render stack is pinned exactly (`Pillow==…`, binary wheel only), because CS-B4 goldens are byte-exact; changing the pin means regenerating goldens deliberately. `service/requirements-test.txt` adds test tooling and the legacy local server's deps.

Tests (clean venv):
```
python3 -m venv .venv && .venv/bin/pip install -r service/requirements-test.txt
.venv/bin/python -m pytest tests            # Creative Studio + legacy compiler suite
```
The Creative Studio real-database tests need PostgreSQL 17 binaries (`PG_BIN`, or Homebrew `postgresql@17`); the suite builds a private throwaway cluster and applies every migration in filename order. The CS-B4 UI tests need `npm ci` in `ui/app`.
