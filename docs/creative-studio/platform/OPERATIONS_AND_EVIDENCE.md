# Deployment, operations and source evidence

CS-M1 design only, observed 30 October 2026. No deployment, tests, SQL mutation, paid provider generation or CPOS mutation was performed by this platform subtask.

## Observed bindings

| Resource | Read-only observation | Implication |
|---|---|---|
| Source repo | /Users/craigkunte/Developer/GitHub/services/image-pipeline, root compiler/FastAPI/React UI | Complete this service; preserve dirty adapter changes and nested historical copy |
| Vercel team | Craig's projects, team_n1rMtzqnARnlXX4LEA8eYlKR | Existing connected hosting account |
| Vercel project inventory | 27 projects returned; no image-pipeline or creative-studio name; no local image-pipeline .vercel/project.json | No existing CS Vercel project binding established; create/link only in M2 after source/identity gate |
| BCI Vercel project | prj_PqNZly5E833w9d62Lwe4ROb6gdWE, brand-ci-studio, Vite, Node 22.x | Reuse deployment pattern; do not deploy CS over BCI |
| BCI production | dpl_FYa8VgtbvrrQmFKsomRcEH5BjyS1, READY; brand-ci-studio.vercel.app | Actual deployed precedent, not proof for Creative Studio |
| Shared Supabase | wwimngjmnuuitowujnif, Intent-to-Systems Framework, eu-north-1, ACTIVE_HEALTHY, PostgreSQL 17.6.1.063 | Existing project selected; no new database subscription |
| Existing extensions | pg_cron 1.6.4, pg_net 0.19.5, pgmq 1.5.1, supabase_vault 0.3.1 | Queue/scheduler/Vault primitives already present |
| Existing BCI scheduler | bci-studio-dispatch, job ID 23, every 15 seconds, active | Existing bounded-stage schedule pattern; do not alter its job |
| CS tables | No public.cs_* rows in table inventory | New service namespace to implement in M2 |
| Historical compiler cloud tables | workspaces/products/artifacts exist, each RLS enabled | Generic tables do not establish CS ownership; preserve and import deliberately |
| Buckets | brand-ci-studio private, limit 52,428,800; reference-images/rendered-outputs/creative-studio absent in targeted inventory | Local compiler bucket assumptions are not deployed; create own private bucket in M2 |
| Local image-pipeline env | SUPABASE_URL hostname matches shared project; service-role key variable present | Presence only; no value disclosed or used. Do not inherit broad key into hosted service |
| Local BCI binding | .vercel/project.json matches the Vercel project above | Consistent local/remote BCI identity |
| Plans and quotas | Vercel team details omitted billing/plan; quota/capacity not returned. Supabase remaining quota not inspected | No claim of free capacity or authority for paid expansion |

Inventory is a timestamped snapshot. It does not prove a current project is runnable or authorize another product's keys. No credentials, encrypted env values, job-secret bodies or private media were printed.

## Build and rollout contract for CS-M2

1. Use the exact root image-pipeline source snapshot reconciled by CS-A1. Retain pre-existing edits in a separately identified compatibility patch. Add hosted service modules there; do not edit the historical nested copy or place production code in Marketing OS.
2. Generate cs_* migrations with the project's migration tooling, including explicit roles/grants/RLS, immutable triggers, constraints, scoped Storage policies, queue and dispatcher definitions. Additive schema first; never rename/drop historical tables. Apply only the CS migration set; do not apply unrelated pending MOS/BCI migrations.
3. Package the three Python function entrypoints and UI with pinned lockfiles/binaries, license notices, FFmpeg codec configuration and artifact hashes. Record compiler source SHA, renderer version, binary SHA, schema version and contract version in a release manifest. Build output must contain no tenant files, sample provider credentials or historical job output.
4. Create/link a Vercel project for this existing source identity, chosen name creative-studio. Name availability is not evidence of prior ownership. Use the existing team; select a region near eu-north-1 (arn1 if available in the account, otherwise the nearest supported EU region and record it).
5. Populate only the new service's required secrets. Register exact auth redirect URLs and exact MOS/BCI API origins. Do not change shared Supabase Site URL or add wildcard production redirects. Keep previews without provider credentials, scheduler or live integration keys.
6. Deploy the complete hosted assembly. Record the exact deployment ID, alias, source SHA, migration IDs, env-variable names, artifact digests and deployment status. An M2 build is allowed; no tests, media trials, browser acceptance, probes or performance experiments are carried out before CS-M3.
7. Keep run admission/worker dispatch disabled for customer work until build handoff. CS-M2 completion means the intended hosted assembly exists and has deployment receipts, not that its behaviour is verified. Enable only the bounded CS-M3 mode after that milestone and explicit acceptance budget/credential authorization.
8. CS-M3 performs the functional, refusal, tenant, failure-injection, restart, performance and paid acceptance work after the assembled build. Record repairs and retests separately. Customer production activation follows acceptance, not a successful build log.

Preview environments use a distinct environment_id and no production queue consumption. For hosted CS-M3 verification use an isolated CS environment/workspaces and storage prefix on the authorized project; production data and provider credential mode are explicitly selected. Shared schema tests must not run in the shared production database; use isolated local/ephemeral Supabase only in CS-M3. A second hosted subscription is not provisioned implicitly.

### Renderer and binary packaging

The build input selects resvg 0.49.0. Official release resvg-linux-x86_64.tar.gz SHA-256 is 216637e75e909e53e3098b0875cd5f7da2fdc8b4359771b4a29b539afff9d3f5; source archive resvg-0.49.0.tar.xz SHA-256 is a1b167d575d0be44e303c76b46fe0aefc5c5748a46979ebae6a7dc3276c2ce8c. These are published upstream checksums observed in M1, not evidence of a downloaded/verified bundle. Pin the release and dependency notices in M2; retain MIT/Apache-2.0 notices and the selected licensing choice. [Release assets](https://github.com/linebender/resvg/releases), [upstream licensing](https://github.com/linebender/resvg).

Select **FFmpeg 9.0.2** source release (observed current on the official download page) and produce a Linux x86_64 build for the Vercel runtime in M2. Record source signature verification, source/archive hash, compiler/toolchain digest, external-library versions, exact configure flags and ffmpeg/ffprobe binary hashes. Use libx264 for H.264, native AAC for audio, PNG/MJPEG and libwebp for static encodes, and only the required scale/crop/overlay/concat/xfade/audio mix/trim/resample filters. Disable unnecessary network protocols; no runtime package download or floating third-party binary URL. The build may use --enable-gpl for libx264 but never --enable-nonfree; retain GPL notices and corresponding source/build recipe alongside deployable distributions. Keep the CLI process boundary rather than linking FFmpeg into compiler code. Do not describe the entire bundle as permissively licensed. [Official source distribution](https://ffmpeg.org/download.html), [FFmpeg licensing](https://ffmpeg.org/legal.html).

The final package must fit the standard 500 MB Python function bundle; fonts/media remain request assets, not bundled customer files. Exact binary compatibility, codec availability, glyph shaping, transitions, output pixels and performance are CS-M3 verification after the assembled hosted build. A build-stage linker/package failure is repaired in M2 without starting a media trial.

### Required environment categories

Public runtime config: SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY, served by /api/v1/config or explicit Vite-safe names only. No secret uses a VITE_ prefix.

Private API: CS_API_DATABASE_URL (restricted role), CS_ENVIRONMENT_ID, CS_RELEASE_ID, CS_MOS_CLIENT_PUBLIC_KEYS, CS_BCI_API_ORIGIN, CS_ALLOWED_ORIGINS. Private worker: CS_WORKER_DATABASE_URL, CS_WORKER_SECRET, CS_CREDENTIAL_ENCRYPTION_KEY_V1, CS_STORAGE_WORKER credentials/refresh material. Private service integration and webhook key references remain workspace/account scoped in encrypted records; never a global platform fallback.

Configuration defaults: CS_ADMISSION_ENABLED=false, CS_DISPATCH_ENABLED=false, CS_PAID_PROVIDER_ENABLED=false. These deployment flags additionally gate database policy; flags alone are not budget authorization. Scheduler Vault secret is unique to CS and is not the BCI worker token. See deployment-contract.json for runtime constants.

## Operating controls

Queue health view: due count, oldest due age, active/stale leases, last dispatch/reconcile receipt, uncertain paid attempts, provider deadline/retention proximity, ingest failures, storage consumption and quota holds. Logs correlate environment/release/workspace/run/node/attempt/work item/fencing token but omit secret payloads. Persist essential operational/audit events in cs_*; platform log retention is not the audit source.

Initial operational thresholds, adjustable without widening authorization: stale heartbeat >90 seconds; leased item >360 seconds without renewal; oldest due work >2 minutes; provider status older than 5 minutes while waiting; any needs_reconciliation, overspend, out-of-scope access or failed handoff. UI shows blocked state and reason. Alert delivery channel is a later explicit configuration; this design does not send messages or create an automation.

Runtime /health reports process version only. /ready reports config completeness, restricted DB connection, own queue/storage availability, recent dispatch and migration compatibility; it does not call a paid model or claim every product capability ready. Provider credentials can show configured/disabled/unknown; no live paid trial is hidden in readiness.

Native request admission rate: 30 mutations/minute/user, 10 compiles/minute/workspace, 2 active runs/workspace, 1 active paid dispatch/workspace and 1 heavy render globally initially. Webhooks: bounded size <=256 KiB, duplicate-safe, per-account rate cap with retryable 5xx when persistence is unavailable. Apply capacity policy atomically. Restrict queue drain to registered release/environment and hold all unknown stages for manual diagnosis.

### Recovery playbooks

- **Worker/release fault:** disable new admission and dispatcher; allow leased local work to finish or expire; preserve acceptance receipts. Roll back Vercel to a recorded compatible deployment, then reconcile stale leases. Never force all running attempts back to queued.
- **Provider outage:** leave accepted requests waiting and retry reads. Hold uncertain submissions. Same-intent replays use the same saved provider idempotency key only under the documented capability. Disable new admissions if errors/budget unknowns exceed policy.
- **Expired upstream URL:** resolve an exact hash/version again through BCI or MOS; persist the owned bytes. Refreshing a pre-dispatch reference is safe only before the frozen paid request is created. After dispatch, changed transport payload requires a new explicit intent.
- **Lost/duplicated webhook:** authenticated status polling is sufficient; inbox dedup and attempt receipt uniqueness prevent double settlement.
- **Storage/DB outage:** stop new paid work before dispatch; retain unsent holds and queued items. If provider already accepted, continue reconciliation when storage recovers before provider expiry. Never report an export complete before owned persistence.
- **Quota exhausted / unknown:** show capacity_blocked operational cause, leave jobs durable, stop new provider spend. No automatic upgrade or alternate paid host.
- **Credential compromise:** disable credential/runtime principal, stop new work, rotate affected service keys; status reconciliation uses separately authorized replacement credentials for the same provider account. Rotate integration signatures and invalidate outstanding launch assertions.
- **Deletion request:** revoke sharing first, append tombstones, cancel eligible work, clear owned objects and provider data where allowed after retention/legal holds. Keep minimal charge/review/audit receipts according to account retention policy; mark withdrawn assets unusable. Database and Storage deletion are separate tracked steps.

### Rollback and backup

Record last compatible deployment ID and supported schema/worker version range. Use expand/contract migrations: old and new workers can read the same additive rows during rollout; stop claiming unsupported stage revisions. A rollback changes code/traffic and dispatcher target; it does not reverse immutable exports, provider charges, reviews, or database history. Never down-migrate/drop CS tables to make an old release start.

Set regular operator-authorized export of cs_* rows and private object manifests once the service is active, preserving checksums, encryption-key version metadata and reference lineage. Database backup alone does not back up Storage bytes. Store snapshots in an approved existing location; no new backup subscription is assumed. Define restore by restoring rows, objects and required old encryption keys, starting with dispatch disabled, reconciling provider receipts, then reopening admission. Backup/restore exercise belongs to CS-M3. Target RPO/RTO are provisional until a recovery exercise; do not advertise an SLA.

## Source anchors

All paths below are read-only observations of the root service or deployed BCI pattern, not claimed new implementation.

| Source symbol | Current role / required change |
|---|---|
| image-pipeline/core/job_compiler.py:165 compile_job; :267 compile_path_to_dir | Preserve deterministic lowering, expose through hosted offline compile stage |
| image-pipeline/core/compile_bundle.py:112 validate_manifest; :222 load_runtime_bundle | Persist canonical artifacts to Storage, verify digest on hydration |
| image-pipeline/core/graph_runtime.py:31 execute_node | Reuse node execution contract; separate async provider submit/poll from local computation |
| image-pipeline/core/run_graph.py:231 run_graph_async | Local full-graph scheduler; do not use as hosted process-lifetime coordinator |
| image-pipeline/ui/server.py:1763 create_graph_run; :1809 asyncio.create_task | Replace hosted route with durable admission; memory registry is not recovery state |
| image-pipeline/ui/cloud_store.py:178 workspace_id_for_slug | Generic tables and service-role adapter; replace in hosted service, preserve for explicit import/local compatibility |
| image-pipeline/ui/artifact_registry.py:241 sync_registry | Existing artifact capability to reconcile, not a missing registry to rebuild |
| image-pipeline/core/adapters/engine_higgsfield.py:109 _resolve_params; :187 execute_plan | Preserve local dirty patch; hosted transport must use workspace Model API credential and safe media fetch |
| brand-ci-studio/api/worker.ts:9 bci_claim_job and :16 bci_finish_step | Existing bounded stage precedent; extend with fencing, classified retry and immutable paid receipts |
| brand-ci-studio/server/net.ts:7 safeFetch | Existing DNS-pinned request pattern; extend to media/decode/process limits |
| brand-ci-studio/supabase/migrations/20261030000300_brand_ci_scheduler.sql:10 | Observed 15-second dispatcher; create a separate CS scheduler |
| brand-ci-studio/supabase/migrations/20261030000400_brand_ci_external_access.sql:3 | External API authorization must not leave stale browser membership |
| brand-ci-studio/vercel.json | Deployed Vite + Python function precedent |

Source roots:
- /Users/craigkunte/Developer/GitHub/services/image-pipeline
- /Users/craigkunte/Developer/GitHub/services/brand-ci-studio

## Documentation evidence and limitations

Official sources were browsed on 30 October 2026. The Supabase changelog markdown fetch was unsupported by the web reader; the HTML changelog and relevant breaking-change entry were read instead. Relevant changes: explicit Data API grants, no extension-version pinning assumptions, and no dependence on deprecated Node 20. Runtime Python/FFmpeg packaging uses pinned application dependencies and binary hashes; it does not attempt to pin hosted extension versions.

Provider facts are primary documentation, not a live account trial. The intended workspace's Model API credential, webhook secret, accessible models, current price and explicit paid media budget still need to be supplied/authorized before paid CS-M3 acceptance. An existing Higgsfield CLI login is not evidence of that API access.

The final hosting choice is settled. Genuine remaining deployment facts are existing account plan/available quota and allocation limits, plus registering the new service project/roles/bucket/secrets as M2 work. These do not justify inventing new paid infrastructure or claiming the system already deployed/tested.

