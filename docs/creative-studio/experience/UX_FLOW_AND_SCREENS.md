# Creative Studio — flow, screens and component architecture

Status: CS-M1 authored design, 30 October 2026. New route and component names below are implementation targets, not implemented functionality.

## Journey and terminology

```text
MOS approved concept ── authenticated handoff ─┐
                                             v
Studio / Campaigns ── New creative ──> Brand & campaign
Library ── Use in a new creative ──────────────┘
                          |
                          v
                   Outputs & direction <──> Workflow
                          |
                          v
                   Review cost and create
                          |
                          v
                   Saved job / Results ──> Compare variants
                          |                       |
                          v                       v
                  Layout or Video <───── Select preferred result
                          |
                          v
                   Export preview ──> Saved export
                          |                 |
                          |                 └── Download pack
                          v
                     Send to MOS ──> MOS review receipt / Open review

Any saved recipe/result ──> Duplicate or new revision ──> Outputs & direction
```

The screen header always names workspace, creative and revision. “Campaign” identifies the imported campaign; it does not mean a newly authored MOS campaign. “Recipe” is a saved arrangement of inputs and steps. “Workflow” is its optional connected view. “Layout” places image layers. “Video” opens a shot list and timeline. Raw port names, hashes, provider IDs and compiler files appear only under **Details**.

The primary desktop journey has three setup sections: **Brand & campaign**, **Outputs & direction**, **Review & create**. Users may revisit completed sections; missing required fields are linked in a persistent readiness summary. Setup is not six blocking modal dialogs. After generation, the same creative opens its results and editing tabs. A user can save and leave at every stage.

## Routes

Routes are workspace-scoped in the browser and authorized independently by the service. The slug/UUID in a URL is context, not permission. `:workspaceId` denotes the service workspace mapped by a verified MOS handoff.

| Route | Screen / responsibility | Entry and exit |
|---|---|---|
| `/w/:workspaceId/studio` | Creative list, current jobs and resumable drafts | Default Studio entry; **New creative**, open existing row |
| `/w/:workspaceId/studio/new` | Create a saved authoring draft or import a verified handoff | Redirects to a durable draft URL; creates the canonical recipe only when its required source references exist |
| `/w/:workspaceId/studio/drafts/:draftId` | Incomplete source/direction setup using the same editing controls | Save and leave; promote to exact request/treatment/recipe when ready; incomplete drafts cannot compile or run |
| `/w/:workspaceId/studio/:recipeId/builder` | Guided sections above; `?section=inputs|direction|review` | Continue, save and leave, Workflow tab |
| `/w/:workspaceId/studio/:recipeId/workflow` | Editable workflow projection of the same recipe | Builder, compile preview, node details |
| `/w/:workspaceId/studio/:recipeId/results` | Result matrix and variant comparison; selected immutable versions | Layout, Video, reuse as input, new variant |
| `/w/:workspaceId/studio/:recipeId/layout/:compositionId` | Image artboards and editable layers | Results, export, revision history |
| `/w/:workspaceId/studio/:recipeId/video/:compositionId` | Storyboard/shot list and bounded timeline | Results, export, revision history |
| `/w/:workspaceId/studio/:recipeId/exports` | Export preflight, finished files, MOS delivery receipt | Download, send to MOS, open exact MOS review |
| `/w/:workspaceId/library` and `/library/:assetId` | Searchable assets; preview/details retain list filters | Use exact version, view lineage, download permitted original |
| `/w/:workspaceId/jobs` and `/jobs/:runId` | Saved progress, attempts, costs and recovery | Resume result/recipe; safe retry or reconcile |
| `/w/:workspaceId/settings/connections` | Authorized account/credential status | Connect action for allowed role; return to blocked creative |

`revision`, selected artboard/shot, library query/sort/page and compare selection are addressable state. The service resolves the exact immutable revision; “current” is a convenience redirect, not a source binding. Cross-workspace navigation clears selected files/results, cancels old subscriptions and fetches the new scope. There is no fallback to the first campaign or another tenant's disk directory.

Legacy `/generate`, `/generate-form`, `/products/:slug`, `/assets`, `/runs`, `/pipelines` and `/workbench` are handled by the migration plan. Existing routes are not removed before historical files and bookmarks have a supported destination.

## Screen inventory

| Screen: primary question | Key components and primary action | Empty / loading / error / recovery |
|---|---|---|
| Studio: What needs attention or can I continue? | `StudioListPage`, `CreativeTable`, `JobSummary`; filter Draft / In progress / Needs attention / Ready; **New creative** | No creatives: plain starter guidance; skeleton rows while loading; loading error retains filters and offers Reload. Never convert a failed fetch to “No creatives”. |
| Brand & campaign: What approved source am I using? | `SourceSummary`, `BrandVersionBadge`, `OfferSelector`, `AudienceSummary`, `ConceptSummary`, `SourceIssueList`; **Continue** | Missing brand package or unapproved concept shows exact missing input and Open Brand CI / Open campaign action. Wrong scope is refused without revealing record details. Expired file links refresh silently while stable IDs remain. |
| Outputs & direction: What am I making? | `OutputPackSelector`, `DirectionFields`, `ReferenceTray`, `ExactCopyFields`, optional `StoryboardDraft`, `ModelPolicyField`; **Review creation** | Default static pack, hero or short video; no mock image results. Drafting assistance, if enabled, is a separately disclosed action that creates a proposed treatment. Unsupported model controls explain which requirement conflicts. |
| Review & create: What will this produce and cost? | `PlanSummary`, per-output thumbnails/placeholders, `CostSummary`, `SourceRevisionSummary`, `ReadinessIssues`; **Create [n] outputs** | Shows provider/model resolved by server, estimate timestamp/expiry, spending ceiling and work list. Stale or missing price/rights/concept/brand information blocks submission with a direct recovery link. The preview itself submits no media job. |
| Workflow: How do inputs become outputs? | `RecipeWorkflowPage`, `RecipeCanvas`, `WorkflowNode`, `NodeInspector`, `StepListAlternative`, same `PlanSummary`; **Review creation** | Add only registered typed steps. Invalid edge remains a local proposal with reason; save/compile reject it. Unsupported imported nodes show an explicit read-only issue, never disappear. “Notes” are annotations, never executable custom operations. |
| Results: Which version should I use? | `ResultMatrix`, `VariantCompare`, `AssetDetails`, `RerunImpactPanel`; **Edit selected** | Rows grouped by output and variant; pending cards show named stage and last update. A partial failure keeps completed outputs usable. One result cannot masquerade as the entire pack. |
| Layout: How should the image read? | `ArtboardSwitcher`, `LayerList`, `ImageStage`, `LayerInspector`, `ExactCopyEditor`, `BrandConstraintNotice`; **Prepare export** | Missing font/asset gets a blocking named issue; original layer reference remains. Low-resolution warning names affected artboard. Unsaved work stays visible with Save failed and Retry. |
| Video: How should the campaign play? | `StoryboardList`, `ShotInspector`, `VideoStage`, `Timeline`, `CaptionEditor`, `AudioTrackInspector`; **Prepare export** | Empty shot slot shows Choose clip / Create clip. Unsupported duration/ref role is explained before submission. Short replacement clip offers trim/reflow choices, never silently changes the story. |
| Exports: Which exact files can I deliver? | `ExportSettings`, `ExportPreflight`, `ExportTable`, `DeliveryPanel`; **Export pack**, then **Send to MOS** | Export processing is a saved job. Failed assembly can retry from retained media. Expired download link renews; unavailable file is an error, not a blank success. MOS outage leaves export downloadable and delivery retryable. |
| Library: Which existing source can I reuse? | Existing gallery evolved into `AssetLibrary`, file/type/rights/brand filters, version drawer | No matches allows clearing filters. Missing original retains metadata/history and names recovery. Reuse pins the selected version; choosing a newer version is an explicit edit. |
| Jobs: What happened and what can I do? | `JobTable`, `RunTimeline`, `AttemptDetails`, `CostSummary`, action by server classification | Reconnecting fetches a fresh snapshot. **Checking provider** has no automatic paid Retry. Terminal failure distinguishes retryable configuration, known provider failure and unresolved outcome. |

## Layout wireframes

Desktop builder preserves the current three-part content relationship: context, decision fields, preview. It shares the neutral shell rather than creating a new application inside every tab.

```text
┌ Workspace / Brand                   Creative Studio      Search  Theme  Account ┐
│ Nav  │ Spring campaign     Draft 3 · Saved              History    More         │
│      │ Builder   Workflow   Results   Exports                                  │
│      ├──────────────────┬─────────────────────────────┬────────────────────────┤
│      │ Brand & campaign │ Outputs & direction         │ Output preview         │
│      │ Outputs          │ Static pack                 │ [1:1] [4:5] [9:16]     │
│      │ Review & create  │ Direction, references       │ Layout placeholders    │
│      │                  │ Exact headline / CTA        │ Brand v3 / Concept v2  │
│      │ Source status    │ Automatic model / Advanced │                        │
│      ├──────────────────┴─────────────────────────────┴────────────────────────┤
│      │ Saved just now                         Back      Review creation       │
└──────┴────────────────────────────────────────────────────────────────────────┘
```

```text
┌ Workspace / Brand             Creative / Composition 4       Saved            ┐
│ Nav  │ Layout   1:1 / 4:5 / 9:16          Undo  Redo     Prepare export        │
│      ├──────────────┬─────────────────────────────┬────────────────────────────┤
│      │ Layers       │                             │ Headline                   │
│      │ Logo locked  │     Exact-size artboard     │ Text / Font / Size / Color │
│      │ Headline     │     Safe guides optional    │ Position / Align           │
│      │ CTA          │                             │ This change: layout only   │
│      │ Image        │                             │ No generation required     │
│      └──────────────┴─────────────────────────────┴────────────────────────────┤
│      │ Original: Result 2 · Brand v3 · Compare to exported version             │
└──────┴────────────────────────────────────────────────────────────────────────┘
```

```text
┌ Workspace / Brand               Creative / Video 2            Saved          ┐
│ Nav  │ Storyboard / Timeline        00:12 / 00:15       Prepare export        │
│      ├────────────────────┬────────────────────────┬─────────────────────────┤
│      │ 1. Opening       │     Video preview      │ Shot 2                  │
│      │ 2. Product       │                        │ Replace / Trim / Volume │
│      │ 3. Benefit       │                        │ Model & source details  │
│      │ 4. End card      │                        │                         │
│      ├────────────────────┴────────────────────────┴─────────────────────────┤
│      │ Picture [Opening][Product][Benefit][End card]                         │
│      │ Text    [Headline]       [Benefit text]       [CTA]                   │
│      │ Captions [      timed exact text       ]                             │
│      │ Audio    [voice]                    / [music track]                  │
└──────┴───────────────────────────────────────────────────────────────────────┘
```

These are structural wireframes. Labels and placeholders denote controls/content; they are not final icons or generated evidence. Existing media is never stretched to fit a preview and UI headlines are not falsely presented as baked into a downloadable original.

## Console, access and responsive rules

- Use the MOS neutral light/dark roles, Inter/system UI text, 20px page heading, 13px record text, 4–6px corners, fine borders, 48px header and 208px/56px collapsible navigation. Green denotes primary action/selection/status; generated media retains its actual color. Do not copy protected Supabase assets.
- Preserve search/sort/page when a record opens in a right panel. Asset thumbnails use a grid only when visual comparison is the decision. Job/source/history collections use semantic tables. Keep one primary action appropriate to the current state.
- A member can see only authorized workspace material. Editing, generation/spend, connection administration, export and MOS handoff permissions are separate server capabilities. Read-only members can inspect exact versions; disabled actions explain the required role without exposing credentials. Studio curation is labelled **Selected**/**Rejected**, not campaign approval.
- Desktop supports graph wiring, pointer/keyboard positioning and timeline drag. Keyboard alternatives include add/connect step forms, layer order buttons, numeric position/size fields, trim fields and Move shot up/down. Dragging is never the sole way to complete the flow.
- Below the editor's useful three-pane width, retain the preview and show one labelled inspector tab at a time. At narrow mobile widths the builder, jobs, result selection, source details, exports and numeric layer/shot forms remain usable; the full workflow uses an ordered step list. Do not shrink an entire desktop canvas to unreadable scale or block access to saved work.
- Use 32px minimum desktop controls and 44px touch targets, explicit labels, text status plus icon, visible focus, skip link, current-route title, reduced motion, and focus restoration after a dialog/panel. Any confirmation dialog traps focus and closes with Escape when safe; source/draft work remains saved.
- Loading uses skeletons in stable geometry. Long jobs show completed/total items and actual stages; no invented completion percentage, elapsed-time ETA or preview. Announce meaningful completion/error state once, not every poll tick.
- Undo/redo operates on draft editing commands. It never cancels a submitted provider job, erases a paid attempt or silently reverses a MOS review decision.

## Source change and exit behavior

Changing workspace exits the current editor after local changes have saved or the user explicitly chooses how to handle a save failure. Switching Builder/Workflow preserves the same draft, selection and validation issues. Browser Back returns to the source collection with its filters. A new revision banner offers **Compare changes** and **Use new version**; it never silently substitutes brand, campaign, font, asset or model revisions.

The successful exit is a saved immutable export and, when selected, a recorded MOS ingestion receipt with a link to exact-version review. “Sent to MOS” does not mean “Approved” or “Published”. A later edit starts a new revision and retains the earlier export and review history.
