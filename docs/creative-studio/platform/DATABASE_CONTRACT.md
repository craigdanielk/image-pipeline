# Database and queue contract

Design only, CS-M1. The accompanying database-contract.json is a machine-readable inventory of required tables, keys, procedures and state enums. It is not a migration and must not be applied. Implementation belongs to CS-M2; migration replay, RLS, concurrency and fault verification belongs to CS-M3.

## Namespace and identifiers

Use public.cs_* for product data and cs_private.cs_* for credentials, runtime principals and privileged helpers. All public tables enable and force RLS; revoke default PUBLIC, anon and authenticated domain-table privileges. Explicitly grant only restricted API/worker roles. Supabase changed new-table Data API exposure; do not depend on default grants, and do not change project-wide defaults for unrelated products. [Exposure change](https://supabase.com/changelog/45329-breaking-change-tables-not-exposed-to-data-and-graphql-api-automatically).

Every scoped row has workspace_id. Every referenced scoped row has UNIQUE(workspace_id,id), or UNIQUE(workspace_id,version_id) for version tables; foreign keys include both scope and identity columns. Account membership owns account-level rows; workspace.account_id is fixed after creation. Only auth.users is a cross-service FK. Upstream workspace IDs are opaque values accompanied by authority and external binding, not foreign keys.

Use UUIDs for object and version IDs, positive integer revisions, timestamptz UTC for time and lowercase 64-character hex SHA-256. VersionRef is {authority,workspace_id,kind,id,version_id,revision,sha256}; AssetRef is {authority,workspace_id,asset_id,asset_version_id,revision,sha256,mime_type}. Public run and operation fields follow the shared contracts packet; cs-operation-v1 is the durable identity before any plan or run exists. An internal row ID used as a version ID must not be confused with the stable logical object ID.

Authoring drafts are mutable, non-executable saved form/graph state with a current_revision and sha256. Promotion resolves required references and inserts immutable request/treatment/recipe versions; cs_admit_compile refuses drafts. Immutable documents include source imports, recipe versions, compile plans, composition versions, asset versions, exports, review receipts, admission records, provider receipts, cost ledger and events. INSERT-only permissions plus triggers reject UPDATE/DELETE except an audited retention procedure that creates tombstones and preserves required receipts. Current pointers/state projections are mutable with optimistic version or event-sequence checks.

## RLS and roles

| Role | Connection | Scope |
|---|---|---|
| Migration owner | M2 deployment context only | Create CS objects/grants. Not a runtime secret |
| cs_api | Transaction pooler, TLS, pool <=2 connections per instance | Account/workspace and request tables, actor/action RLS; no worker-lease or ledger settlement authority |
| cs_worker | Transaction pooler, TLS, pool <=2 | Claim/heartbeat/result/receipt/ledger and object rows; no membership or budget-authorization changes |
| authenticated | Browser Supabase JWT | No domain table grants; Storage access only through exact short-lived upload/download capabilities |
| CS Storage machine user | Supabase Auth JWT, server only | creative-studio bucket worker prefixes only; no domain PostgREST grants |
| anon / PUBLIC | None | No product data access |

API transaction context is derived from a verified bearer token. Set request.jwt.claims and CS external assertion context via SET LOCAL inside the transaction; never session-global pooled settings. Actor ID in JSON cannot override the authenticated subject. The external context is usable only by cs_api and only after dual-auth validation; it expires within the request. cs_api RLS separately checks action for insert/update using USING and WITH CHECK. SELECT policy is present where UPDATE requires it.

JSON/array-held local VersionRefs and AssetRefs also pass an insert-time database validator using a fixed kind-to-CS-table map: referenced version exists in the same workspace and its revision/digest match. Immutable document links use scoped relational junctions where practical; no unchecked UUID array is treated as a foreign key. Global catalogue references may point only to CS-owned global catalogue rows.

The worker has intentional access across CS workspaces to process jobs, but not other product tables. Its claim routine ignores arbitrary workspace/header overrides and derives all scope from the claimed work item. All following worker procedures require matching item ID, token, fencing token and scope. Storage RPC/helper has exact bucket/path predicates; prefix guessing is insufficient. The storage capability helper is the only narrowly privileged lookup needed by authenticated Storage RLS. It validates auth.uid(), expiry, operation and exact key in cs_private with fixed search_path and no PUBLIC execution.

## Admission transaction

Pre-run admission inserts cs_operations, its first cs_operation_events row, an idempotency receipt and an operation-owned work item in one transaction. It enqueues the wake ID atomically or through the same transactional outbox. Kinds are compile, brand_package_import and treatment_authoring. POST W/plans, W/imports and W/treatment-drafts return 202 with Location W/operations/{id}; GET W/operations/{id} projects the shared cs-operation-v1 schema. There is no synchronous 201 plan response. The compile operation yields an exact execution-plan VersionRef only when it completes; no run is required. Package import yields verified immutable import/package refs and mirrored AssetRefs. An operation's input/admission identity is immutable; state/result projection is backed by its own event sequence.

cs_admit_compile accepts only a complete cs-recipe-v1 VersionRef and has null admission/cost. cs_admit_brand_import requires the authenticated approved BCI export reference and also has null admission/cost. These operations have no paid attempts. cs_admit_treatment_authoring requires an explicit paid action, creative-request/policy refs, a versioned supported text-model capability/price, intended workspace credential and approved nonzero budget. It reserves the per-operation ceiling before author_treatment may dispatch. The same attempts, receipts, unknown-charge rules and account budget lock apply; no fake accepted treatment, recipe, plan or run exists. persist_treatment_draft creates only an immutable draft treatment with operation/attempt provenance. Explicit acceptance later inserts a new accepted version.

cs_admit_run(actor context, workspace, plan version, idempotency key, request hash, maximum_cost_micros, expected_event_sequence) performs:

1. Verify workspace access, producer action, external binding/applicability if any and current source approval.
2. Acquire an account budget lock; read immutable plan digest, catalogue revision, output envelope and credential mode.
3. Lookup (workspace, operation='run.create', key). Return the saved object for the same canonical hash, or conflict for a mismatch.
4. Refuse unknown price/unsupported model, zero/unapproved budget, excessive active runs or insufficient free allowance. Lock relevant budget-policy rows and compute settled + unresolved holds across the rolling window.
5. Insert run, DAG node rows, budget reservations and initial work items; insert idempotency receipt and run event atomically.
6. Send pgmq wake IDs in the same transaction. If not using the extension within that function, use an outbox row in that transaction and let the dispatcher deliver it. Do not commit a run before its durable runnable record exists.

There is one reservation per intended billable run node/attempt or operation step/attempt. Pure local stages require no provider reservation. A partial rerun creates a child run/plan with parent_run_id and selected nodes, reuses exact valid upstream AssetRefs, and reserves only affected billable nodes. It never mutates old successful versions or approvals.

## Lease and event invariants

cs_work_items fields: id, workspace_id, environment_id, run_id, node_id, operation_id, operation_step_key, stage, stage_revision, dedupe_key, due_at, state, attempt_count, lease_owner, lease_token, lease_until, fencing_token, heartbeat_at, payload_ref, created_at, updated_at.

- Exactly one complete owner pair is present: run_id + node_id, or operation_id + operation_step_key; the other pair is null. Composite workspace FKs bind runs/nodes and operations. operation_step_key is a server-defined stable allowlisted step, never an executable expression. Work items, attempts, reservations, receipts and ledger entries preserve the same owner; insert validators reject any linked attempt/reservation owner mismatch.
- Unique(environment_id,dedupe_key); partial index on environment_id,due_at where state in queued/retry_wait.
- Claim under row lock with SKIP LOCKED; change state to leased, increment fencing_token, generate lease_token, set lease_until=now()+360 seconds.
- Heartbeat compares lease token/fence and lease_until>now() and records heartbeat_at. The hard lease remains claim_time+360 seconds; the 240-second application deadline fits inside it. Once expired, the old worker cannot revive it.
- Completion compares token/fence/state/unexpired lease and inserts one immutable stage-result receipt. Unique(work_item_id,fencing_token,result_kind). Only after this commit can downstream work become queued.
- Each run event consumes one sequence allocated under the run row lock; each operation event uses its operation row lock. Unique(workspace_id,run_id,sequence) and Unique(workspace_id,operation_id,sequence) are separate streams. expected_event_sequence rejects stale user mutations with 409; workers use lease CAS and serialize the correct owner event insertion.
- Queue duplicates are harmless: only one lease can be active for one work-item epoch. Queue visibility is not used as the paid-submission lock.
- A paused deployment, disabled scheduler, lack of worker capacity or missing wake message leaves a visible queued state; never mark progress based on dispatch HTTP 2xx alone.

## Crash matrix

| Crash point | Durable evidence | Recovery |
|---|---|---|
| Before admission commit | None | Client retry with same key may admit |
| After admission, before notification | Run or operation + runnable item/outbox | Sweep restores notification |
| After claim, before dispatch marker | Lease + prepared attempt | Expired lease can resume preparatory work |
| After dispatch marker, receipt missing | Frozen request + provider key + unknown charge | Same-intent provider replay only with documented support; otherwise needs_reconciliation |
| Provider ID saved, browser/worker disappears | Acceptance receipt | Scheduled status GET by saved account/ID |
| Webhook repeated/out of order | Inbox event ID + provider request ID | Deduplicate, authenticated status reconcile; no terminal downgrade |
| Bytes uploaded before DB finalize | Immutable staging object | Matching content may be adopted by a new valid local-stage attempt; otherwise orphan GC |
| Finalization committed, response lost | Result receipt + output refs | Idempotent read returns saved result |
| Old worker returns after takeover | Smaller fencing token | Reject its DB commit; do not overwrite objects |
| MOS delivery response lost | Export + handoff key/request hash | Query/replay MOS ingestion with same key; no duplicate asset version |

No lease expiry alone is permission to create another paid generation. An accepted provider request stays waiting_provider even if the local work item's lease expired.

## Provider/cost state

Run and operation states: queued, running, waiting_provider, needs_reconciliation, budget_blocked, authorization_blocked, cancel_requested, completed, failed, cancelled.

Node states: pending, queued, running, waiting_provider, needs_reconciliation, budget_blocked, authorization_blocked, succeeded, failed, cancelled, skipped.

Attempt states: prepared, dispatching, submitted, waiting_provider, ingesting, succeeded, failed, uncertain, cancel_requested, cancelled.

Cost states: reserved, settled, unknown. Money uses integer USD micros. Unknown retains an unresolved ceiling in budget availability; no failure path substitutes zero. Immutable attempt admission plus events derives current state. provider_request_id appears once in an acceptance receipt and must remain constant. The same provider receipt can settle at most one attempt. Receipt/account/owner mismatch is quarantined. Operation attempts additionally bind operation_step_key and step_input_sha256. Public operation attempts and receipt_ids are projections from their scoped immutable records, not mutable UUID lists.

If an exact idempotent Higgsfield replay returns a queued acceptance receipt for already-completed work, that is not a state regression; immediately schedule a status GET. Freeze webhook and input URLs in the transport payload, encrypt it at rest, and do not rehydrate it with refreshed URLs. Cancellation response without charge evidence does not settle costs.

## Required procedures

All names are design contracts; the exact argument records must be generated from the shared schemas during M2. Use typed fields, not a generic arbitrary JSON mutation API.

- cs_create_workspace, cs_set_membership, cs_bind_external_workspace: native owner or validated service principal; unique binding; immutable native/external mode.
- cs_save_authoring_draft, cs_promote_authoring_draft: optimistic revision/hash; promotion requires complete references and inserts immutable source/treatment/recipe records. Drafts are never executable.
- cs_save_recipe_version, cs_import_source, cs_save_composition_version: action/expected-revision checks; immutable insert; source hash/rights validation.
- cs_admit_compile, cs_admit_brand_import: atomically admit a pre-run operation and operation-owned work, return 202 + Location; no provider reservation.
- cs_admit_treatment_authoring: explicit paid authority, capability/price snapshot and account budget lock; operation-owned ceiling and work, never an accepted treatment.
- cs_get_operation, cs_request_cancel_operation, cs_reconcile_operation: current scope/action plus expected_event_sequence for commands; reconcile cannot authorize a new ambiguous paid attempt implicitly.
- cs_finish_operation: fenced completion attaches durable scoped result refs and appends an operation event; compile produces plan, import produces package/source refs, authoring produces draft treatment.
- cs_admit_run, cs_request_cancel, cs_admit_rerun: transaction rules above; version-bound intent and idempotency.
- cs_claim_work, cs_heartbeat_work, cs_finish_work, cs_reschedule_work: worker only; lease/fence CAS; stage allowlist (including bounded render_video_transition); output scope and exactly-one run/node or operation/step owner.
- cs_prepare_attempt, cs_mark_dispatching, cs_record_acceptance, cs_record_provider_event: worker only; frozen request and write-once ID; dedup receipts.
- cs_reserve_budget, cs_settle_cost, cs_mark_cost_unknown, cs_release_reservation: called within admission/receipt procedures; ledger dedup; authorization for budget-policy changes is separate.
- cs_create_upload_intent, cs_finalize_asset, cs_create_download_grant, cs_publish_export: exact workspace/path/digest; original and derived roles distinct.
- cs_record_review, cs_enqueue_handoff, cs_record_handoff_receipt: bind exact exports/source versions; review cannot be reattached to changed bytes.
- cs_dispatch_due, cs_reconcile_due, cs_gc_orphans: scheduler-only; environment pinning, capacity caps, bounded batches and audit receipts.

Append audit events for every mutation with actor/runtime principal, action, workspace, input digest, affected IDs, event/receipt IDs and time. No secrets or signed URLs in the audit payload. Availability/readiness is based on actual dependency status and queue health; data truth is not a UI field.

