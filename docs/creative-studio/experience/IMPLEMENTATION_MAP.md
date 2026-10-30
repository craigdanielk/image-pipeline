# Creative Studio — implementation map and preservation rules

Status: CS-M1 architecture only, 30 October 2026. Paths in the first three tables are relative to `/Users/craigkunte/Developer/GitHub/services/image-pipeline`. They describe CS-M2 implementation targets; none was changed for this architecture contribution.

## Existing files to extend

| Existing path | Preserve | Required change / ownership |
|---|---|---|
| `ui/app/src/App.tsx` | React/Vite/router entry, existing product/campaign/library routes | Add workspace-scoped Studio routes and the authenticated handoff route. Redirect legacy creation entries only after the import/resume adapter exists. CS-B4. |
| `ui/app/src/design/GlassShell.tsx`, `GlassNavRail.tsx` | Persistent navigation/topbar/content hierarchy, skip link, existing route relationships | Apply neutral console tokens, explicit workspace/brand context, Studio/Library/Jobs navigation and account controls. Historical “Glass” class names can remain compatibility names; do not rewrite unrelated pages for naming. CS-B4, authenticated shell from CS-B1. |
| `ui/app/src/design/tokens.css`, `design/glass.css`, `index.css` | Existing semantic roles and media-stage layout classes | Map to accepted neutral light/dark console roles and compact metrics; keep imagery unmodified. Avoid page-specific hardcoded brass/dark overrides. Preserve useful board/gallery geometry. CS-B4. |
| `ui/app/src/TenantContext.tsx`, `WorkspaceContext.tsx` | Workspace selector concept and refresh API boundary | Replace public tenant/disk-root trust with authenticated workspace membership from CS-B1. Browser storage is a preference only. Abort/clear old-scope state on switch; do not fall back to another tenant or service-repo jobs. Never send local `jobs_root` to hosted customers. CS-B1/B4. |
| `ui/app/src/api.ts` | One request helper boundary | Add authenticated scoped calls, typed refusals, stale-version conflict handling and idempotency keys; resolve stable assets to permitted short-lived URLs. Do not expose service credentials/provider URLs. CS-B1/B2/B3 supply backend; CS-B4 consumes. |
| `ui/app/src/types.ts` | Compatibility types for legacy ProductSpec/Job/Artifact records | Add imports for generated/shared cs-* contract types; avoid a second handwritten schema. Keep compatibility adapters separate from new canonical objects. CS-B2/B4. |
| `ui/app/src/pages/BoardPage.tsx` | Existing React Flow interaction, node presentation, inspector pattern, product/campaign/reference affordances | Extract canvas/node/inspector components. Replace component-local source state and board-specific submission with `RecipeSession`. Semantic edges edit real recipe connections. Existing intent-row/runtime-rail presentation becomes one optional details expansion. Custom operations no longer silently disappear. CS-B4. |
| `ui/app/src/GraphCanvas.tsx`, `CompilerNode.tsx`, `InspectorPanel.tsx` | Inspectable compiled-graph view and technical details | Keep compiled-plan inspector read-only. Reuse graph primitives without creating another editable recipe store. CS-B4. |
| `ui/app/src/pages/GeneratePage.tsx` | Familiar product/campaign/output field grouping | Evolve into guided builder or thin compatibility entry to it; remove independent generation state/submission after migration. CS-B4. |
| `ui/app/src/components/BriefModal.tsx`, `NewShotWizard.tsx` | Existing shot intent, references and feedback entry points | “New shot” opens/patches a saved recipe/shot draft. Optional assistance proposes frozen treatment. Route all creation through the same preview/admission path. No direct `/api/execute` from a modal. CS-B4/B3. |
| `ui/app/src/pages/ProductsPage.tsx`, `ProductDetailPage.tsx`, `ProductNewPage.tsx`, `components/ProductForm.tsx` | Existing products, references and historical jobs | Preserve browsing/onboarding. Add generic offering/service input summaries for imported requests rather than inventing physical geometry. Import must retain original assets and ProductSpec lineage. CS-B2/B4. |
| `ui/app/src/pages/CampaignsPage.tsx`, `CampaignNewPage.tsx` | Existing campaign/style records and navigation | Distinguish legacy style target from approved MOS campaign source; “Create creative” selects/pins one and starts Studio. Do not manufacture MOS campaign approval from a legacy style record. CS-B2/B4. |
| `ui/app/src/components/ArtifactGallery.tsx`, `pages/AssetsPage.tsx`, `OutputStrip.tsx` | Thumbnails, product/campaign filters, comments, variant grouping, restore/reject history | Evolve to exact-version library/result components; retain missing-file status, selected/rejected curation and provenance. Add compare and use-as-input. Approved legacy gallery status becomes curation evidence, not a MOS approval. CS-B4. |
| `ui/app/src/pages/RunsPage.tsx`, `RunBar.tsx` | Existing run entry point and visible progress | Use durable snapshots/event cursors from CS-B3; job table plus details/attempt/cost drawer. Connection loss is reconnecting, not job failure. CS-B3/B4. |
| `ui/app/src/CreatorModeContext.tsx`, `pages/PipelinesPage.tsx`, `pages/LegacyWorkbench.tsx` | Existing advanced/debug views and historical operator workflow | Developer visibility is a presentation preference, never authorization. Hosted debug routes require explicit service permission; no arbitrary local path execution. CS-B1/B4. |

## Proposed new frontend modules

These are an implementation decomposition, not a request to scaffold placeholders during CS-M1. Small adjacent components may be combined during implementation when they share one responsibility.

| Target path | Responsibility |
|---|---|
| `ui/app/src/studio/RecipeSession.tsx` | One canonical recipe/composition session, including the initial non-executable authoring draft; save queue, expected-version checks, undo/redo, view projections and dirty state |
| `ui/app/src/studio/recipeCommands.ts` | Typed semantic edits; no provider calls or hidden defaults |
| `ui/app/src/studio/recipeProjection.ts` | Builder sections, authoring-graph nodes/edges and optional compiled-detail projection derived from the same source |
| `ui/app/src/studio/StudioApi.ts` | Contract-based recipe/source/plan/run/export/handoff client, delegated to shared authenticated transport |
| `ui/app/src/studio/useSavedJob.ts` | Initial snapshot, cursor-based events, reconnect/fallback refresh and lifecycle-safe unsubscribe |
| `ui/app/src/studio/pages/StudioListPage.tsx` | Draft/job/result collection with preserved filters |
| `ui/app/src/studio/pages/RecipeBuilderPage.tsx` | Brand & campaign, outputs & direction, review & create sections |
| `ui/app/src/studio/pages/RecipeWorkflowPage.tsx` | Optional editable workflow, registered-step palette and accessible step-list alternative |
| `ui/app/src/studio/pages/ResultsPage.tsx` | Per-output result matrix, comparison, selection and partial rerun impact |
| `ui/app/src/studio/pages/ExportsPage.tsx` | Export preflight/preview, saved file versions, download pack and MOS receipt |
| `ui/app/src/studio/components/SourceSummary.tsx` | Pinned brand/campaign/offer/audience/copy facts, source freshness/change and recovery links |
| `ui/app/src/studio/components/ReferenceTray.tsx` | Stable asset/version/role picker; rights and capability-aware reference limits |
| `ui/app/src/studio/components/PlanSummary.tsx`, `CostSummary.tsx`, `RerunImpactPanel.tsx` | Read-only current plan, known/unknown quote/actuals, retained/affected work and server-admissible action |
| `ui/app/src/studio/components/VersionHistoryPanel.tsx` | Exact revisions, compare, restore as new draft; approval binding shown separately |
| `ui/app/src/studio/components/VariantCompare.tsx` | Synchronized pan/zoom for images, matched playback/time controls for clips, distinct version labels |
| `ui/app/src/studio/composition/ImageCompositionPage.tsx` | Artboard/layer/preview/inspector workspace |
| `ui/app/src/studio/composition/ScenePreview.tsx`, `LayerList.tsx`, `LayerInspector.tsx` | Declarative SVG/image/text preview plus keyboard/numeric equivalent edits; no persisted DOM or canvas bitmap as source |
| `ui/app/src/studio/composition/MaskEditor.tsx` | Versioned raster region mask tied to base asset pixels/hash; separate from exact layout layers |
| `ui/app/src/studio/composition/VideoCompositionPage.tsx` | Storyboard/timeline/preview/inspector workspace |
| `ui/app/src/studio/composition/ShotList.tsx`, `Timeline.tsx`, `CaptionEditor.tsx`, `AudioInspector.tsx` | Bounded shot sequencing, frame-based timing and exact overlays/audio controls |
| `ui/app/src/studio/composition/geometry.ts`, `timing.ts` | Shared pure coordinate/frame arithmetic used by all editing controls; server independently enforces invariants |
| `ui/app/src/design/ConsoleTable.tsx`, `DetailPanel.tsx`, `PageHeading.tsx`, `StateNotice.tsx` | Local primitives matching MOS morphology; avoid importing MOS product runtime across repositories |

Keep current React Flow for workflow interaction. Use declarative scene data and browser SVG/image/text/video preview for the bounded composition controls; no new unrestricted design-canvas framework is required by this architecture. The authoritative server render adapters are selected in the platform blueprint, and the frontend consumes their saved preview/export assets. A browser implementation cannot substitute its own fonts, layout rules or export bytes.

## Compiler/service touch points consumed by the UX

| Existing / proposed service area | Required contract |
|---|---|
| `core/spec_validator_v2.py`, `compile_specs_v2.py`, `spec_compiler.py`, `job_compiler.py` | Keep v2 compatibility. Add a versioned cs-* request/treatment lowering lane in CS-B2; reject unrepresentable inputs rather than fabricate geometry or silently drop fields. |
| `core/compile_bundle.py`, `compile_validator.py`, `schemas/` | Build the inspectable immutable plan/manifest from saved source. Semantic hashing excludes authoring-only layout; provenance retains exact source objects. New schema versions live in the compiler repository during CS-M2, with these architecture files as the source contract. |
| `core/node_registry.py`, `graph_ir.py`, `graph_compiler.py` | Register typed transforms/composition/export operations. Validate DAG, input role/type/count and output coverage. No executable custom label bypass. |
| `core/graph_runtime.py`, `run_graph.py`, `graph_persistence.py`, `ui/workspace_runs.py` | CS-B3 provides durable attempts/leases, snapshots, event sequence, output reuse and idempotent submission. Frontend targets exact plan/step IDs, not a local `compile_dir`. |
| `core/adapters/engine_higgsfield.py`, provider catalogue | CS-B3 exposes supported server-verified controls, quotes and receipts. Existing uncommitted adapter changes must be reconciled and preserved by source owner, not overwritten by an editor refactor. |
| `ui/artifact_registry.py`, `ui/cloud_store.py`, `ui/server.py` | Extend existing registry/cloud/server boundaries into the hosted workspace service. Local paths are importer details, not hosted public identities. Do not expose `/media?path=...` or arbitrary compile paths to browser clients. |
| New composition render adapters | Exact image scene render; normalized clip/card stages; bounded timeline/audio assembly; preview/thumbnail/export. Each records source hashes, renderer version and output bytes. CS-B4 composition inputs; platform worker integration CS-B1/B3/B5. |
| Versioned API from contract blueprint | Non-executable draft save/promotion, recipe save/load, source choices, compile/quote, admit run, job snapshot/events, composition save/preview, export, signed download and MOS delivery. No extra UI-specific backend that bypasses these invariants. |

The API transport and worker paths are owned by the platform/contract blueprint. Proposed frontend modules consume that API; they do not add a public counterpart for every legacy local operator route. Optional assistance that previously depended on the locally authenticated Claude CLI is not treated as a hosted capability. It must use an admitted server adapter with separate budget/receipt, or the UI offers manual direction entry.

## MOS consumer handoff map

Paths here are relative to `/Users/craigkunte/Developer/GitHub/services/marketing-os`. Existing NS-10631 owns these changes; CS-B4 does not duplicate that work.

| MOS path | Consumer change |
|---|---|
| `marketing-os-ui/dashboard/src/marketing-os/StudioView.tsx` | Preserve Studio route and selected content context. Show approved source summary, current Studio job/export and **Open Creative Studio**. Replace local prompt compiler and browser-owned provider polling with service handoff/status. |
| `marketing-os-ui/dashboard/src/marketing-os/MarketingOSCommandCentre.tsx` | Preserve workspace/offer navigation and `focusTradeId`; resolve it to an approved exact source request before opening the service. Returning from Studio selects the corresponding MOS content/review row. |
| `marketing-os-ui/dashboard/src/marketing-os/CampaignPathPanel.tsx` | `media.generate` prepares or invokes the versioned Studio service contract. Prompt readiness cannot be presented as a completed generated asset or approved media. |
| `marketing-os-ui/dashboard/src/server/campaignPath.ts`, `marketing-os-ui/dashboard/src/server/generation.ts` | Scoped server-to-service request, asset ingestion and cost/provenance mapping. Never read compiler/Brand CI/CPOS runtime tables directly. |
| `marketing-os-ui/dashboard/api/compile.ts` | Compatibility boundary during migration: route eligible new work to the approved service adapter; preserve readback/reconciliation of old attempts. Do not silently resubmit old provider jobs through the new path. |
| `marketing-os-ui/dashboard/src/compiler/job.ts` | Keep historical payload interpretation as needed; remove new-source authoring responsibility after consumer cutover. No second prompt/model router in MOS. |
| `marketing-os-ui/dashboard/src/marketing-os/MediaGalleryView.tsx` and review/delivery views | Display immutable ingested export versions and explicit MOS review receipts. Keep historical assets and their actual lineage limitations. |

MOS uses only `mos_*` product tables and its own RPCs, with shared `auth.users` identity. Studio recipe/composition/run data stays in the Studio service. A browser deep link carries an opaque handoff reference and allowed return path; never provider secrets, complete campaign JSON or a bearer token in the query string. MOS server authenticates and scopes the exchange.

## Preservation and migration rules

1. Reconcile the current root image-pipeline source and source owner's dirty files before implementation. Do not edit the nested historical `services/aragon/image-pipeline` copy in parallel. Keep original jobs, references, CAD text, campaign documents and compiled bundles intact.
2. Add a bounded import adapter that maps legacy job/spec/artifact identities to service-owned immutable records with original path/hash/import receipt. Importing files creates no provider call and grants no approval. Existing `reference/` and `refs/` conventions are both recognized; absolute disk paths remain migration metadata only.
3. Historical products without ProductSpec remain readable. The service does not force CAD extraction to view, preserve, download or reuse a legacy asset. When a new production operation requires facts absent from its imported source, explain the missing input and allow a user-supplied valid source.
4. Legacy `/generate` maps to the user's saved recipe or new Builder after explicit workspace resolution. `/generate-form` and shot modal become alternate entry points to that same session. Old pipeline/workbench links remain operator history surfaces where safe, rather than auto-running a migrated recipe.
5. Preserve the gallery's real files, grouping, comments and rejection history. Old “approved” curation is stored as curation. It does not become Brand CI approval, MOS client approval or licensed-use proof. Missing original files are marked missing; do not fabricate a placeholder success or discard history.
6. Keep current shell/navigation, gallery relationships and useful board/inspector positioning. Token alignment supplies the neutral console system. Do not perform a wholesale marketing redesign, replace media with decorative samples or mix a brass legacy canvas with a newly tinted shell.
7. A current open tab may be editing an old revision during migration. Treat unsupported schema as read-only with “Open historical version”; any conversion is a new revision with a mapping report, never destructive in-place JSON rewrite.

## CS-M2 implementation order and handoff

| Build stage | Concrete result | Dependency |
|---|---|---|
| 1. Authenticated shell and contract client | Existing routes load scoped service records; source/import adapters preserve historical assets | CS-B1 and CS-B2 |
| 2. Shared recipe session | Builder, Workflow and legacy creation entries edit/save the same canonical state | Saved request/treatment/recipe contracts |
| 3. Costed creation and persistent results | Plan preview, capability controls, background jobs, comparison and partial-rerun UX | CS-B3 API and durable state |
| 4. Composition | Exact image layers and bounded storyboard/timeline write the shared versioned composition | Asset/font storage and renderer adapters |
| 5. Exports and consumer handoff | Stored previews/files/manifests, download pack and exact MOS ingestion receipt | Renderer/runtime + existing NS-10631 adapter |
| 6. CS-B5 assembled build | Source/build/deployment references and operating handoff recorded | Complete UI/API/worker integration |

This order does not start testing during architecture or build. All behavioral, visual, accessibility, provider and end-to-end verification belongs to CS-T1 after CS-B5, as approved. The later evidence must exercise the actual saved journey: two-workspace isolation; page closure/restart recovery; partial image and video edits; exact text/logo export; brand version change; ambiguous provider failure/cost; MOS version-bound review. Listing these obligations is not passing them.

The experience work has no independent blocker. Missing runtime/auth/brand-package/render capabilities are implementation dependencies assigned above. Paid acceptance credentials and its explicit ceiling are future CS-M3 prerequisites. These documents made no CPOS changes, tests, runtime edits, deployments or paid-media requests.
