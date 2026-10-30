# Creative Studio v1 API outline

Architecture only, 2026-10-30. All routes below are target CS-M2 surfaces unless the compatibility table explicitly identifies an existing route. Schemas describe saved responses; create/revision bodies contain editable fields only. The server assigns IDs, revisions, timestamps, actors and hashes. No route is implemented by these documents.

## Transport and authorization

Base: configured Studio origin + `/api/v1`. Workspace resources use `/workspaces/{workspace_id}` (`W` below). User requests require a validated bearer identity and current workspace capability. MOS gateway requests additionally require trusted server authentication and a current MOS authorization/eligibility response for that same identity/action. Secrets never appear in frontend config. Origins and callback destinations are deployment configuration, never arbitrary caller URLs.

Every mutating request supplies a UUID `Idempotency-Key`. The server persists `(workspace_id, operation, key, canonical_request_sha256, response_resource_id)` with the effect. An identical retry returns the existing response with `Idempotent-Replayed: true`. Different payload under the same key returns 409. Every revision also supplies `If-Match: "<current artifact sha256>"`; stale input returns 409 and the current VersionRef. Worker/provider replays use separate keys and do not reuse the API request key.

Success response: `{data: <document>, request_id: <uuid>}`. Collections: `{data: [...], next_cursor: string|null, request_id}`; maximum page size 100. Failure: `{error: {code, message, retryable, field_paths, related_ids}, request_id}` using the common error type. 201 creates an immutable record; 202 means durable asynchronous admission, never successful generation. 200 reads or replays a prior result. Download tickets set `Cache-Control: no-store`; other responses are private and scoped. Pagination cursors are opaque, scope-bound and must not grant access.

All client-triggered paid actions are explicit: treatment authoring, initial generation, deliberate rerun/replacement. Saving a form, moving canvas nodes, reopening a page, downloading, polling or compiling does not start generation. Compile uses the saved catalogue estimate rather than a live provider call.

## Workspace and upstream connections

| Method/path | Body or query | Result and invariant |
|---|---|---|
| `GET /workspaces` | Cursor | Current user's Studio memberships only |
| `POST /workspaces` | `{name:string}` | Owned workspace; no CPOS identity |
| `POST W/connections` | `{authority:"marketing-os"|"brand-ci-studio", external_workspace_id:uuid}` | Connection ID after both API authorities validate binding; no implicit identity equality |
| `GET W/connections` | — | Safe connection IDs/scope/state; no credentials |
| `POST W/connections/{id}/refresh` | `{expected_external_workspace_id:uuid}` | Current access and eligibility, or explicit authorization block |
| `POST W/brand-packages/imports` | `{connection_id:uuid, package:VersionRef}` | 202 `cs-operation-v1` of kind `brand_package_import`, with `Location: W/operations/{id}`; exact package and copied asset refs appear only on durable completion |
| `GET W/brand-packages/{version_id}` | — | Exact imported package plus separate current eligibility status; never silently upgrades |
| `GET W/provider-catalogues/current` | — | Effective immutable workspace catalogue VersionRef/document; no provider keys |
| `GET W/provider-catalogues/{version_id}` | — | Exact historical revision; enabled/expiry eligibility checked separately at compile |

Required **Brand CI-owned additions**, using its existing authenticated account model:

- `POST /api/v1/brands/{brand_id}/packages` with `{version_id:uuid, approval_receipt_id:uuid}` produces/reuses the exact `brand-package-v1`. It refuses a currently returned/unapproved receipt, missing required metadata or mismatched account/brand. It neither edits the guide nor approves it.
- `GET /api/v1/packages/{package_version_id}` reads that immutable package. Return current approval state separately so historical provenance remains readable without being executable by default.
- `POST /api/v1/assets/{asset_id}/versions/{version_id}/download-ticket` with `{expected_sha256, purpose:"preview"|"reference"|"embed"|"redistribute"}` returns a short-lived `DownloadTicket` after current authorization/rights checks. Existing `/assets?brand=...` signed download behavior can back this exact-version adapter.
- `GET /api/v1/approvals/{receipt_id}/applicability?version_id=...&action=...` returns the honest policy, current outcome, source version, original creator and receipt actor. Caller-supplied role headers alone are insufficient.

Required **MOS-owned adapter additions**, reusing `brand-studio.ts` gateway patterns and NS-10631:

- `GET /api/creative-studio?action=authorize&workspace_id=...&action_id=...` returns `{user_id,workspace_id,role,allowed,policy_revision,expires_at}` after current MOS membership/capability checks.
- `POST /api/creative-studio/eligibility` with `{workspace_id,action:"generate"|"handoff"|"publish",source_versions:VersionRef[]}` returns `EligibilityReceipt`. It checks only the existing policy for that action and pins actual concept/copy/brand mapping/claim/destination revisions. A 60-second maximum validity prevents treating an old browser snapshot as current authority.
- `GET /api/creative-studio/sources?workspace_id=...&concept_id=...` exports immutable source versions for a new Studio request. It never returns MOS provider secrets or imports a compiler.

## Authoring and compilation

| Method/path | Body | Result |
|---|---|---|
| `POST W/authoring-drafts` | `{name:string}` | Empty non-executable draft; server initializes all nullable selections and empty working state |
| `GET W/authoring-drafts/{id}` | — | `cs-authoring-draft-v1`; save/leave/resume from the first screen |
| `PUT W/authoring-drafts/{id}` | Editable draft fields; `If-Match` | Next optimistic draft revision; no compile or generation |
| `POST W/authoring-drafts/{id}/promote` | `{request:VersionRef,treatment:VersionRef,recipe_fields:{template,nodes,outputs,composition,authoring_view}}`; `If-Match` | Atomically persists canonical recipe and marks draft promoted after required source/treatment validation; a repeated key returns the same recipe |
| `POST W/creative-requests` | Editable fields of `cs-creative-request-v1` | 201 saved request VersionRef/document |
| `POST W/creative-requests/{id}/revisions` | Complete replacement of editable fields; `If-Match` | New immutable revision; old runs unaffected |
| `GET W/creative-requests/{id}/versions/{version_id}` | — | Exact request |
| `POST W/treatment-drafts` | `{request:VersionRef,authoring_policy:VersionRef,budget:Budget,instruction:string}` | 202 `cs-operation-v1` of kind `treatment_authoring`, with `Location: W/operations/{id}`; immutable funding/attempt receipts; result is a draft treatment, never accepted automatically |
| `POST W/treatments` | Human-authored draft direction/shot/source fields plus request ref | 201 draft treatment; no model call |
| `POST W/treatments/{id}/revisions` | Edited direction/shot/source fields; `If-Match` | New `draft` revision, even if prior one was accepted |
| `POST W/treatments/{id}/accept` | `{version:VersionRef}`; `If-Match` | New accepted immutable treatment revision with actor/time |
| `POST W/recipes` | `{request,treatment,template,nodes,outputs,composition,authoring_view}` | One canonical saved recipe for both views |
| `POST W/recipes/{id}/revisions` | `cs-recipe-command-v1`; `If-Match` | Atomic command application and new saved recipe |
| `GET W/recipes/{id}/versions/{version_id}` | — | Same recipe for guided and canvas views |
| `POST W/plans` | `{recipe:VersionRef,catalogue:VersionRef}` | 202 `cs-operation-v1` of kind `compile`, with `Location: W/operations/{id}`; atomic `cs_admit_compile` queues bounded compilation. Completed operation returns exact plan VersionRef; deferred failure has diagnostics and no runnable plan |
| `GET W/plans/{version_id}` | — | Exact plan, safe program and source refs |
| `POST W/plan-diffs` | `{before:VersionRef,after:VersionRef}` | `{changed_node_ids,reused_node_ids,affected_output_ids,estimated_incremental_micros,maximum_incremental_micros}`; read-only computation |

The source version ref passed to a revision/acceptance command must match the target ID, workspace and `If-Match`. A draft can be saved with unbound inputs, but a runnable plan cannot. Unknown node types and dangling references are always errors. View metadata revisions may reuse the same compile hash; the exact saved recipe revision is still retained as provenance.

Pre-run work has one common status resource:

| Method/path | Body/query | Result |
|---|---|---|
| `GET W/operations/{id}` | — | Safe `cs-operation-v1` snapshot: own state/event sequence, input refs, committed result refs/assets, attempts, cost/receipt IDs and diagnostics |
| `POST W/operations/{id}/cancel` | `{reason:string,expected_event_sequence:int}` | 202 cancel request; stops new work/paid dispatch, reconciles accepted provider work without promising a refund |
| `POST W/operations/{id}/reconcile` | `{attempt_id:uuid,expected_event_sequence:int}` | 202 evidence/status reconciliation or catalogue-proven same-intent replay for treatment; no new key or changed request |
| `GET W/operations/{id}/receipts` | Cursor | Same immutable provider/cost receipt contract, owned by operation plus step rather than run plus node |

An admitted operation and its initial work item exist before 202 is sent. The original idempotency key returns that same operation after response loss, including if it has since completed. Recipe absence, identity denial or invalid admission shape returns 4xx immediately; failures discovered by queued source resolution/compilation appear as `failed` with structured `error` on the operation. Callers poll the operation and fetch its resulting immutable plan/package/treatment reference only after completion. None of these responses invents a run or grants treatment acceptance.

## Assets and editable composition

| Method/path | Body/query | Result |
|---|---|---|
| `POST W/assets/uploads` | `{name,mime_type,size_bytes,expected_sha256,rights}` | `{upload_id,upload_url,expires_at}` scoped to a new private object; not an available asset yet |
| `POST W/assets/uploads/{id}/complete` | `{expected_sha256}` | AssetRecord after server reads size/MIME/hash and commits immutable asset version |
| `GET W/assets` | Cursor, optional kind/filter | Scoped library; deletion status and permissions are metadata outside immutable asset bytes |
| `GET W/assets/{id}/versions/{version_id}` | — | Exact AssetRecord and current rights applicability |
| `POST W/assets/{id}/versions/{version_id}/download-ticket` | `{expected_sha256,purpose}` | DownloadTicket; no mutation of asset/version identity |
| `POST W/assets/{id}/rights` | New Rights receipt; `If-Match` latest rights receipt | Append-only rights revision; never changes historical evidence |
| `POST W/compositions` | Editable fields of `cs-composition-v1` | Composition based on prior recipe and exact successful assets |
| `POST W/compositions/{id}/revisions` | Complete replacement of editable composition fields; `If-Match` | New composition; no generation merely from saving |
| `GET W/compositions/{id}/versions/{version_id}` | — | Editable layers/timeline with exact refs |

Rendering preview uses the same versioned layout engine and assets as final export, at a declared preview scale. Preview files have their own derivative identities and cannot be mistaken for final exports. Requesting a final export compiles/adopts an export recipe and admits a normal durable run; no special browser-only rendering authority exists.

## Execution, cancellation and receipts

| Method/path | Body/query | Result |
|---|---|---|
| `POST W/runs` | `{plan:VersionRef,budget:Budget,accepted_maximum_micros:int}` | 202 persisted `cs-run-v1`; quote/ceiling/credential mode must match plan and current policy |
| `GET W/runs/{run_id}` | — | Safe run/attempt snapshot; no signed provider URLs or credentials |
| `GET W/runs/{run_id}/events` | `after_sequence:int` or `Last-Event-ID` | Ordered `cs-run-event-v1` SSE; client can resume after disconnect |
| `POST W/runs/{run_id}/reruns` | `{plan:VersionRef,node_ids:slug[],reuse:SourceOutput[],budget:Budget,accepted_maximum_micros:int,expected_event_sequence:int}` | New child run after affected-descendant/cost preview; a deliberate generation intent |
| `POST W/runs/{run_id}/cancel` | `{reason:string,expected_event_sequence:int}` | 202 cancel request; does not promise refund or abort of already accepted provider work |
| `POST W/runs/{run_id}/reconcile` | `{attempt_id:uuid,expected_event_sequence:int}` | 202 status/billing reconciliation or allowed same-intent replay; never a new provider key/body |
| `GET W/runs/{run_id}/receipts` | Cursor | Safe immutable provider/cost receipts |
| `GET W/exports/{version_id}` | — | Complete `cs-export-v1`; in-progress export stays on run status |
| `POST W/exports/{version_id}/download-ticket` | `{expected_sha256,disposition}` | Fresh ticket for the immutable final file |
| `POST W/handoffs/marketing-os` | Editable handoff fields and exact export refs | 202 durable transfer; authenticates existing connection, then calls MOS adapter |
| `GET W/handoffs/marketing-os/{id}` | — | `{handoff:VersionRef,state:queued|transferring|complete|blocked|failed,items:[{export,status,receipt_id|null,error|null}],receipt:IngestionReceipt|null}` |

Internal routes are inaccessible to ordinary user roles:

- `/internal/work/claim` accepts `{worker_id,capabilities,limit}` and returns scoped work item IDs, payload ref, lease expiry and fencing token. `/internal/work/{id}/advance` requires expected state and current fencing token. Public run state never exposes the lease token.
- `/webhooks/higgsfield/{provider_account_binding_id}` accepts only verified raw-body deliveries. It binds request ID to a pre-existing attempt, deduplicates provider event ID and durably records the receipt before acknowledging. It cannot create a run or choose a workspace from payload input.
- Reconciliation/polling resolves the saved provider response URL against the catalogue's HTTPS origin/path/request-ID constraints. Neither browser nor webhook supplies a credential-bearing status destination.

MOS receives a proposed `/api/creative-studio/ingestions` call with `cs-mos-handoff-v1`. Its own handler fetches manifests/files from the configured Studio API, checks hashes, and calls its owned ingestion/version/rights RPC path. It returns `cs-mos-ingestion-receipt-v1` only after all declared items exist. A separate MOS review action may later approve exact versions; transfer itself always starts pending review.

## Refusal codes

| HTTP | Code examples | Meaning and recovery |
|---|---|---|
| 400 | `invalid_json`, `unsupported_schema_version` | Correct contract; nothing executed |
| 401 | `authentication_required`, `session_invalid` | Sign in again |
| 403 | `capability_denied`, `scope_mismatch`, `rights_denied` | Correct current permission; do not retry with another workspace ID |
| 404 | `resource_unavailable` | Missing/inaccessible resource; do not disclose cross-workspace existence |
| 409 | `revision_conflict`, `idempotency_conflict`, `approval_stale`, `source_revision_changed`, `source_unavailable`, `reconciliation_required` | Inspect current source or previous intent; never overwrite/rekey automatically |
| 422 | `invalid_recipe`, `asset_hash_mismatch`, `font_asset_missing`, `copy_revision_mismatch`, `unsupported_model_control`, `video_limit_exceeded`, `provider_idempotency_mismatch` | Fix the explicit input; failure is not a successful preview |
| 429 | `budget_exceeded`, `workspace_concurrency_limit` | No new dispatch; show saved work and required budget/capacity |
| 502/503 | `upstream_unavailable`, `catalogue_expired`, `worker_unavailable` | Preserve admission/uncertainty state; bounded retry only under the recorded policy |

Errors include no tokens, filesystem paths, raw SQL, provider authorization headers or stack traces. Internally retain correlated evidence with redaction and least-privilege access. Unexpected errors after dispatch report uncertain state; a 5xx response does not authorize another paid generation.
