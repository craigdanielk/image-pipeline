# Existing compiler and service compatibility map

Inspection date: 2026-10-30. This maps observed source symbols to the architectural contract; it does not claim implementation or behavioral verification. The parent CS-M1 source inventory owns commit/working-tree reconciliation. No source below was changed by this contracts lane.

## Existing image-pipeline core

Paths are relative to `/Users/craigkunte/Developer/GitHub/services/image-pipeline`.

| Existing file/symbol | Observed contract | Preserve and extend in CS-M2 |
|---|---|---|
| `schemas/product-spec-v2.schema.json`; `core/prompt_compiler_v2.py::_product_block` | Structured geometry/materials/protected regions; v2 schema requires `product.geometry.description` | Keep physical ProductSpec v2 reader. New creative-request offering lane emits service context without empty/fake CAD or geometry preservation. Do not force service/service-group through v2 validation. |
| `schemas/creative-spec-v2.schema.json` | Campaign/scene/scene-lock structure; required product-spec ID | Retain existing creative semantics as reusable lowering primitives. New request envelope supplies brand/offer/audience/concept/copy/format revisions and separate accepted treatment. No assertion that v2 accepts new fields. |
| `schemas/edit-spec-v2.schema.json`; `core/spec_validator_v2.py` | Structured operation and reference-role constraints | Map supported region edits to exact source/mask AssetRefs. Reject unsupported controls against the catalogue. Pure logo/copy edits use composition instead of generative repainting. |
| `schemas/execution-request-v1.schema.json` | Engine/model, output, references and dry-run; mainly image-oriented; local paths allowed | Compatibility-only input. Add output-specific duration/frame/dimension validation in the new lane; public requests cannot carry local paths or arbitrary runtime params. |
| `core/compile_validator.py::validate_for_compile` | Chooses existing spec lane and rejects mixed languages | Add an explicit Studio lane and adapters; preserve existing v1/v2 behavior. Services lower to the same compiler ownership, not a MOS prompt compiler. |
| `core/prompt_compiler_v2.py::compile_v2` | Deterministic structured assembly to ProviderPrompt v1 | Reuse scene/product/operation assembly; add accepted-treatment, brand/reference and service-offering handlers. AI authoring remains outside compile. |
| `core/job_compiler.py::compile_job`, `compile_job_multi_op` | Legacy job-v1 lowering and per-operation plans | Keep import/read compatibility. Import a legacy file into a scoped immutable source revision with original schema/hash/path metadata; never execute arbitrary caller filesystem paths. |
| `core/capability_resolver.py::resolve`, `_CAPABILITIES` | Inline four-model capabilities, reference support, params, plan hash; retries default to 3 | Externalize/version catalogue; route by exact effective model; add endpoint JSON Schemas, video/control/pricing/funding constraints. Replace undifferentiated retry count with before-dispatch vs same-intent reconciliation vs deliberate new intent. |
| `schemas/provider-prompt-v1.schema.json`, `schemas/provider-execution-plan-v1.schema.json` | Prompt intermediate and concrete provider plan | Preserve as legacy artifacts. New plan envelope binds catalogue, exact sources, costs, outputs and runtime slots. Adapter accepts a frozen effective provider call, never source specs. |
| `core/compile_bundle.py::write_compile_dir`, `build_manifest`, `load_runtime_bundle`, `runtime_artifacts_view` | Hashed compile directory, manifest pointers; source specs explicitly forbidden in runtime bundle | Retain compiler/runtime separation. Store immutable artifact blobs and public IDs instead of client `compile_dir`. Keep legacy byte/object hashes alongside new semantic RFC8785 compile hash. |
| `schemas/compile-manifest-v1.schema.json` | `source.kind` permits job-v1/spec-bundle | Existing `ui/server.py::compile_board` emits `board-ui`, outside that published enum. Resolve through explicit compatibility manifest extension, not silent schema coercion or a claim the current schema already matches. |
| `core/graph_compiler.py::compile_execution_graph`; `core/graph_ir.py::validate` | Execution-only DAG and typed ports | Lower source recipe nodes to registered generation/transform/composition/export nodes. Keep DAG/port checks and explicit bound output slots. |
| `core/node_registry.py::NodeSpec`, `validate_node_type`, `validate_node_ports`, `validate_payload` | Registry of typed handler/port schemas | Add versioned media/layout/video/export nodes and catalogue-bound config validation. Unknown operations cannot be skipped and presented as success. |
| `core/graph_runtime.py::execute_node`, `_exec_engine_generate`, `_exec_result_as_ref`, `input_hash` | Executes planned nodes and reuses result as reference | Execute one durable bounded stage per lease, resolving exact AssetRefs. Preserve result reuse but bind original attempt/hash/rights. |
| `core/run_graph.py::run_graph_async`, `_schedule`; `core/graph_persistence.py::find_idempotent_run`, `start_graph_run`, `record_node_run`, `lookup_compile_cache` | Local scheduler and persistence/cache helpers exist | Reuse semantics behind the new durable cs_* service. Existing helpers are not proof of tenant isolation or crash recovery. Strict workspace+operation idempotency, leases/fencing and event projections replace optional persistence. |
| `core/adapters/engine_higgsfield.py::execute_plan`, `generate`, `cost` | Plan execution through CLI; `--wait`; local uploads/path resolution | Keep legacy operator adapter. Hosted adapter uses authenticated endpoint-specific submission/status receipts and persistent waiting, without an inherited CLI login or synchronous full-run wait. Current provider idempotency is capability-scoped, not guessed from the old adapter. |

## Existing Studio surfaces and storage

| Existing symbol | Observed behavior | Target mapping |
|---|---|---|
| `ui/brief_drafter.py::draft_prompt_template` | AI drafts a job-v1 prompt template | Explicit metered treatment draft; freeze and accept before compile. Keep source/model/template provenance. |
| `ui/server.py::compile_board`, `BoardCompileRequest` | Product/campaign/operations lowered to job-v1; hardcoded model; random board job ID | New recipe command writer plus plan compiler. Stable node identities and shared guided/canvas semantics; selected model is visible and pinned. |
| `ui/app/src/pages/GeneratePage.tsx`, `BoardPage.tsx`, `GraphCanvas.tsx`; `tools/graph_to_reactflow.py::graph_v1_to_reactflow` | Guided intent and board surfaces, typed wiring, runtime rail, per-node reruns | Reuse components/projection where appropriate. Both author the same recipe; runtime rail is read-only execution detail. Remove custom-op silent exclusion and filesystem compile-directory authority. |
| `ui/server.py::create_graph_run`, `_graph_runs`, `get_graph_run` | `asyncio.create_task` and in-memory run status | Persistent `cs-run-v1`, event sequence, durable work item admission and server-driven completion. Browser may close; memory loss cannot lose the run. |
| `ui/server.py::board_artifacts_load` | Approved local artifact becomes `artifacts.load`, path passed into node | Exact AssetRef + current rights/applicability; gallery approval is distinct from MOS publication approval. |
| `ui/artifact_registry.py::_artifact_id`, `sync_registry`, `get_artifact`, `approve_artifact` | Registry exists; identity derived from path; status flags | Scoped logical asset/version UUIDs + byte hashes; preserve path-derived ID only as import provenance. Approval becomes an immutable policy-specific receipt. |
| `ui/cloud_store.py::signed_url`, `upload_object`, `upsert_artifact`, `create_run` | Optional cloud functions using generic product/artifact/run tables | Do not inherit generic table ownership or credentials. Service writes only cs_* through the owned boundary and private bucket. Existing cloud availability is not an accepted resource binding. |
| `ui/workspace_runs.py::create_run`, `update_run`, `set_approval` | Filesystem run/approval state | Import historical records with explicit legacy provenance; new run/events/approval records live in service-owned durable state. |

## Brand CI exporter

Paths are relative to `/Users/craigkunte/Developer/GitHub/services/brand-ci-studio`.

| Existing source | Available now by source inspection | Needed adapter behavior |
|---|---|---|
| `api/service.ts`: GET `/versions/:id` | Immutable version, latest approval, signed approved/draft PDFs | Package exact version+receipt; never “latest” selection after pinning. Source approval eligibility response is separate from immutable export. |
| `api/service.ts`: POST `/approvals` | Owner role required; approved/returned receipt with actor/rationale/time; approved derivative rendered first | Preserve actual owner policy. Add policy identity/source creator/applicability export; do not claim independent review that was not enforced. |
| `api/service.ts`: GET/POST `/assets`; `server/core.ts::storeAsset`, `assetUrl`, `signed` | Asset UUID, SHA-256, MIME, size, source URL, private path and 900-second signed downloads | Explicit versioned asset adapter, rights receipts, exact-version renewable tickets and approved-PDF AssetRecord. Existing synthetic `assets.brand-ci.invalid` URLs are renderer identities, not public download endpoints. |
| `server/render.ts`: `bci_publish_version` call | `digest = sha(JSON.stringify(document))`; PDF SHA in QA metadata | Preserve legacy digest as `sha256-js-json-stringify-v1`; separately compute canonical document SHA and approved-PDF bytes. Never relabel original digest as JCS. |
| `server/document.ts::normalizeReferences`, `validateDocument` | Captured asset aliases/source evidence; proposals/unverified items retained | Translate verified guide structure to package fields, preserving evidence and blockers. Do not treat captured fonts or website logos as licensed by capture alone. |
| `bci_assets`, `bci_versions`, `bci_approvals` migrations | Existing service-owned storage/version/approval records | Rights and package publication are wrapper extensions, not changes to the preserved `vendor/brand-ci/engine`. No direct bci_* access from Studio or MOS. |

## Marketing OS consumer

Paths are relative to `/Users/craigkunte/Developer/GitHub/services/marketing-os`.

| Existing source/symbol | Observed limitation or reusable contract | CS-M2 handoff |
|---|---|---|
| `marketing-os-ui/dashboard/api/brand-studio.ts` | MOS checks own workspace and `mos_has_role`, then calls Brand CI gateway with user and service authentication | Reuse API-only authority pattern for Creative Studio; never share product-table access. |
| `marketing-os-ui/dashboard/src/compiler/job.ts::compileJob` | Thin JobV1 constructor and endpoint map; no complete brand/reference source bundle | Replace active consumer responsibility with request/recipe references and API invocation under NS-10631. Keep historical reader only; no second prompt compiler. |
| `marketing-os-ui/dashboard/api/compile.ts::handler`, `finishGeneration` | Start/poll flow, per-workspace secret resolution, results written to `mos_creative_assets`; status URL read from metadata | Consumer tracks Studio run IDs/export refs. Studio owns media execution/credentials. Finalization becomes server-driven; returned provider URL cannot receive a MOS credential. |
| `marketing-os-ui/dashboard/src/server/generation.ts::routeGeneration`, `HIGGSFIELD_MODEL_COSTS` | Separate hardcoded model/cost list | Read the same Studio capability/catalogue revision for media controls and estimates. MOS retains organization budget/policy authority; no divergent effective model. |
| `marketing-os-ui/dashboard/src/server/campaignPath.ts::campaignPathCapability` (`media.generate`) | Calls `mos_generate_media_version`; stores planned/prompt-ready asset version | Retain exact concept/copy/brand-source lineage; route execution to Studio. Do not call prompt readiness a finished media asset. |
| `campaignPathCapability` (`asset.version`, `asset.rights.record`, `approval.version.bind`) | Existing MOS version lists, rights writer, approval RPC/state | Keep these MOS authorities; add external Studio refs/cost/rights details without accepting Studio/BCI approval as content approval. |
| `marketing-os-ui/dashboard/src/server/deliveryLoop.ts::deliveryLoopCapability` (`asset.ingest`) | Guarded fetch, byte SHA and size; calls `mos_ingest_asset_version` | Extend with explicit Studio export-version/manifest hash, expected hash check, durable owned file storage, source-binding idempotency and lineage/rights preservation. |
| `supabase/migrations/20261012230000_mos_delivery_loop.sql::mos_ingest_asset_version` | Requires production role and approved concept; increments asset version; stores external URL/hash/MIME/size, estimated cost 0 | Preserve current permission/concept gate. Add true cost states and source/receipt bindings; external cost unknown is not zero. A retry must reuse the same imported version. |
| `mos_approve_content`, `mos_content_approval_state`, existing delivery/publish path | Exact MOS review/version eligibility authority | Continue to require current applicable approvals after import. Preserve source changes and invalidation; no direct publishing from Studio. |

## Bounded implementation gaps and decisions

No factual gap prevents authoring this architecture. The following are explicit CS-M2 work, not missing architecture decisions: Brand CI package/rights exporter and usable font files; current model endpoint catalogue and credential-mode admission; cs_* durable state and private assets; service-aware compiler lane; shared recipe revisions; image/video composition; MOS durable ingestion and exact review mapping. Source access, schema presence, captured files and the existing inline model list do not prove any of these are built or funded.

Historical imports require provenance and rights review; they are not automatically publication-ready. Existing hashes retain algorithm labels. Existing CPOS/project/repository identities are untouched, and CPOS remains outside every product API above. CS-M1 closes only through the parent architecture reconciliation; this package makes no readiness/acceptance claim.
