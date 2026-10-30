# Creative Studio — experience and composition architecture

Date: 30 October 2026. Scope: CS-M1 / CS-A2, existing image-pipeline project `95f25a77-9b10-4703-9945-7447a992eec9`. This is an authored architecture contribution. It does not mark a task complete, implement the product, or establish tested readiness. CS-M2 implements the design; all testing, generated-media trials and acceptance follow under CS-M3.

## Decisions

1. The default entry is a guided campaign builder. **Builder** and **Workflow** are two projections of one saved `cs-recipe-v1`; switching views never translates between independent copies. The workflow view extends the existing React Flow board.
2. **Layout** is the separate image composition surface, and **Video** is the storyboard/timeline surface. Calling all three surfaces a canvas would hide their different jobs. Workflow connects steps; Layout places exact assets; Video orders shots and their overlays.
3. Approved Brand CI and campaign facts are pinned inputs. The Studio can change creative treatment, layout and output choices; it does not become a second brand or campaign approval authority. Physical ProductSpec geometry is optional for services.
4. A generation result is retained as an original asset. Typography, logos, CTA, captions and title/end cards are independent editable composition layers. Exact text or brand marks never depend on a model drawing them correctly.
5. The first release supports static `1:1` (1080 × 1080), `4:5` (1080 × 1350), `9:16` (1080 × 1920) artboards and a `9:16` campaign video. The selected video authoring limits are 30 seconds, six clips of at most 10 seconds each, 1080 × 1920 and 30 fps. These are implementation bounds, not measured performance claims. Per-provider input and duration limits can be narrower; custom dimensions are unavailable in v1.
6. Paid actions occur only from a server-issued, current cost preview. A partial rerun names the retained results, new work, affected exports and estimated additional cost. Unknown provider outcome or charge never appears as a free failure.
7. Save, generation progress, export progress and MOS delivery are server-owned. Closing the page cannot cancel a job; a disconnected browser cannot declare it failed. The interface resumes from a saved snapshot and event cursor.
8. The visual system follows the accepted neutral MOS console: restrained borders, compact records, persistent workspace context and right-side details. Existing image-pipeline page relationships, media previews, stored assets, recipes and historical routes are preserved during migration.

## Deliverables

| Document | Implementation decision it owns |
|---|---|
| [Flow and screens](UX_FLOW_AND_SCREENS.md) | Entry/exit, routes, screens/components, wireframes, access and responsive behavior |
| [Recipe and state](RECIPE_AND_STATE.md) | Guided/workflow mapping, editing commands, invalidation, save/version/run/review lifecycle and recovery/cost UX |
| [Composition model](COMPOSITION_MODEL.md) | Image layer representation, bounded video model, supported editing and export behavior |
| [Implementation map](IMPLEMENTATION_MAP.md) | Exact existing files to extend, proposed modules, legacy migration and CS-B4 ownership boundary |

The service contract definitions in the adjacent architecture contract package are authoritative for wire schemas. This experience package uses `brand-package-v1`, `cs-creative-request-v1`, `cs-treatment-v1`, `cs-recipe-v1`, `cs-execution-plan-v1`, `cs-run-v1`, `cs-composition-v1`, `cs-export-v1`, `cs-provider-catalogue-v1` and `cs-mos-handoff-v1`. It does not fork those schemas.

## Source evidence read

| Source | Observed fact used here |
|---|---|
| MOS `AGENTS.md`, `docs/marketing-os/RELAY.md`, `SPEC_V1.md`, `PROGRAMME_STATE.md` | MOS data independence, exact-version production/review lineage, current phase and scope. Creative Studio's approved milestone order supersedes running relay tests in this architecture scope. |
| `docs/marketing-os/CREATIVE_STUDIO_RESEARCH_AND_INTAKE_2026-10-29.md` and approved `planning/creative-studio-project-2026-10-29` packet | Guided builder plus optional shared workflow; separate existing compiler service; image/video loop and MOS ownership. |
| image-pipeline `ui/app/src/App.tsx`, `pages/BoardPage.tsx`, `pages/GeneratePage.tsx`, `components/BriefModal.tsx` | Board, older generate form and brief modal currently carry distinct state/submission paths; board uses React Flow, typed runtime nodes and per-node reruns. |
| image-pipeline `core/graph_ir.py`, `schemas/creative-spec-v2.schema.json`, `schemas/edit-spec-v2.schema.json`, `ui/server.py` | Typed DAG/port validation, immutable-source/compiler boundary, target-node slicing, existing compile/run routes and in-process run-status registry. |
| image-pipeline `components/ArtifactGallery.tsx`, `pages/AssetsPage.tsx`, `pages/RunsPage.tsx`, `ui/artifact_registry.py`, `ui/cloud_store.py` | Existing gallery, status/comments, product filtering, run list, asset registry and cloud helper code must be evolved, not described as absent. |
| MOS `marketing-os-ui/dashboard/src/marketing-os/StudioView.tsx`, `CampaignPathPanel.tsx`, `MarketingOSCommandCentre.tsx` | MOS currently has a thin compiler prompt/preview, browser polling, campaign media request and Studio navigation; it becomes a consumer handoff, not another editor/compiler. |
| MOS `STYLESEED.md`, `.styleseed/effective-rules.md`, `docs/marketing-os/design/CONSOLE_REDESIGN_2026-10-28.md`, dashboard `DataTable.tsx`, `PageHeading.tsx`, `index.css` | Accepted console morphology, table/detail interaction, neutral tokens, keyboard/mobile expectations. |
| image-pipeline `AGENTS.md`, `CLAUDE.md`, `design/GlassShell.tsx`, `design/GlassNavRail.tsx`, `design/tokens.css` | Source execution policy; existing shell layout and legacy brass/dark tokens. The July narrative is not substituted for current code evidence. |

The compiler source was inspected at HEAD `e98cf4bf3558f87279ca518d71ee58530f6cf039`. Existing modified tenant config, provider adapter, CAD content and untracked job material were read only and preserved. The nested historical `services/aragon/image-pipeline` tree is not a second implementation target.

## Boundaries and dependencies

- Source/ownership reconciliation remains CS-A1's concern. Hosted authentication, storage, queue, worker and render-resource selection belong to the platform blueprint. No hosted choice is inferred from a UI file.
- Brand package export and campaign request import are CS-B2 contracts. The experience can show a missing approved package and its recovery link before that integration is available; it cannot label a draft package approved.
- Credential configuration and explicit paid-media ceiling are required before paid CS-M3 acceptance. They do not block these architecture documents.
- MOS consumer changes belong to existing NS-10631. The Studio owns export production and delivery receipts; MOS owns ingestion, exact-version review and publishing eligibility.
- No Figma file round-trip, arbitrary plug-ins, advanced VFX, unrestricted animation curves, multi-camera editing, arbitrary multitrack NLE or native mobile editor is promised.

No experience-design blocker remains. The UI design is concrete enough to implement once the complete CS-A2 architecture gate and its platform/input dependencies are satisfied. No tests, paid calls, deployments or runtime edits were performed in this contribution.
