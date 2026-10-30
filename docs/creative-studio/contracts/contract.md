# Creative Studio v1 — executable contract architecture

Date: 2026-10-30. Scope: CS-M1 / CS-A2. Canonical implementation repository: `services/image-pipeline`; project `95f25a77-9b10-4703-9945-7447a992eec9` (`CREATIVE-STUDIO`). These are architecture artifacts for implementation in CS-M2. No runtime implementation, schema validator, compiler, test, media generation or deployment was executed to produce this package. Testing starts in CS-M3 after the hosted build gate.

The approved task packet, MOS `SPEC_V1.md`, and the standalone Brand CI boundary control this design. The old ARAGON source stays historical. Existing compiler languages remain compatibility inputs; the new contracts do not imply that today's handlers already accept them.

## 1. Contract files and authority

| Contract | Owner | Purpose |
|---|---|---|
| `common.schema.json` | Shared API vocabulary | Exact versions, assets, rights, approvals, formats, budgets, costs and download tickets |
| `brand-package-v1.schema.json` | Brand CI | Export of one approved guide revision and its source files |
| `creative-request-v1.schema.json` | Creative Studio; MOS sources remain MOS-owned | Campaign/offering/audience/copy/output input snapshot |
| `treatment-v1.schema.json` | Creative Studio | Human or AI direction, explicitly accepted before compilation |
| `recipe-v1.schema.json` | Creative Studio | One saved graph edited by guided builder and canvas |
| `authoring-draft-v1.schema.json`, `recipe-command-v1.schema.json` | Creative Studio | Non-executable initial work and atomic shared recipe revisions |
| `provider-catalogue-v1.schema.json` | Creative Studio | Versioned endpoint schemas, controls, pricing and supported retry behavior |
| `dispatch-envelope-v1.schema.json`, `provider-receipt-v1.schema.json` | Creative Studio | Frozen private transport and immutable provider evidence |
| `execution-plan-v1.schema.json` | Creative Studio | Immutable resolved program, cost ceiling and source lineage |
| `operation-v1.schema.json` | Creative Studio | Durable pre-run compilation, package import or paid treatment authoring |
| `run-v1.schema.json`, `run-event-v1.schema.json` | Creative Studio | Mutable run snapshot and append-only events/attempt evidence |
| `composition-v1.schema.json` | Creative Studio | Exact image layers and bounded video timeline |
| `export-v1.schema.json` | Creative Studio | Final bytes, rendition details and full provenance |
| `mos-handoff-v1.schema.json` | Creative Studio | Exact export set offered to MOS |
| `mos-ingestion-receipt-v1.schema.json` | MOS | Evidence that MOS accepted those exact bytes and versions |

The `$id` base `https://schemas.creative-studio.invalid/v1/` is a reserved schema identifier, not a deployed service. Bundle these files into both implementations; never fetch arbitrary schema URLs during a customer request. JSON Schema [2020-12](https://json-schema.org/draft/2020-12/json-schema-core) defines the structural dialect. Configure validation to assert formats, reject unknown fields, reject duplicate JSON keys, and report a JSON Pointer for each invalid field. The semantic rules below are equally normative; JSON Schema alone cannot verify references, DAGs, sums, hashes or permissions.

## 2. Identity, scope and data boundary

1. Studio owns `cs_*` rows/RPCs and private `creative-studio` objects. Brand CI owns `bci_*`; MOS owns `mos_*`. Only `auth.users` is shared identity. No cross-service product-table queries, FKs, CPOS imports or CPOS-dependent customer actions are permitted.
2. A Studio workspace UUID, MOS workspace UUID and BCI account UUID are different identifiers. A service-owned connection record maps them after both services authorize the connection. A body parameter, email address, matching UUID, client-supplied role or CPOS tenant is never sufficient authorization.
3. Studio checks its membership for standalone users. MOS-origin requests use the user identity plus trusted service authentication and a fresh MOS authorization response for the exact workspace/action. Workers use narrowly scoped job identity and a recorded admission; they do not persist user bearer tokens in recipes or plans. Current membership and action eligibility are rechecked before a new paid dispatch and handoff.
4. Every stored object, queue item, attempt, reservation, private storage path and lookup carries Studio `workspace_id`. Internal composite references require matching workspace IDs. Cross-service references are checked only through configured APIs. A mismatched scope returns `scope_mismatch` without exposing the other workspace's object.
5. Supabase storage has its own access policies; an asset ID is not authorization. Service credentials stay server-side. This follows [Storage access control](https://supabase.com/docs/guides/storage/security/access-control). Shared identity validation does not substitute for current workspace authorization; see [Supabase JWT guidance](https://supabase.com/docs/guides/auth/jwts).

## 3. Version and hashing rules

Saved artifacts have logical `id`, immutable `version_id`, integer `revision`, `workspace_id`, UTC timestamps and `sha256`. IDs are lowercase UUIDs; stable node/layer/shot/artboard IDs are case-sensitive slugs. A mutation creates a new version. It never overwrites bytes, changes a prior recipe, or rewrites an earlier approval. Run state is deliberately different: it is a projection of ordered events, identified by `run_id` and `event_sequence` rather than a fake immutable version.

Use these separate digests:

| Digest | Exact preimage | Meaning |
|---|---|---|
| Asset `sha256` | Raw bytes | File integrity; independent of URL, path or MIME claims |
| Artifact `sha256` | RFC8785 canonical UTF-8 saved document with only its top-level `sha256` omitted | Exact version integrity, including provenance and view metadata |
| `text_sha256` | Exact UTF-8 text, with no trimming or normalization | Exact approved visible/spoken copy |
| Semantic source hashes | Projections below | Stable compiler inputs without audit/view noise |
| `compile_hash` | RFC8785 canonical UTF-8 `execution_plan.program` | Repeatable compiler result |
| `node_input_sha256` | Compile hash, node ID, resolved dependency output AssetRefs, input roles/ordinals and immutable operation config | Actual inputs to one execution attempt |
| Operation `step_input_sha256` | Operation request hash, step key, exact input version/asset refs and versioned authoring-policy/adapter config | Actual inputs to one pre-run paid treatment attempt |
| `dispatch_envelope_sha256` | Provider account binding, endpoint, request JSON, webhook and expiry header; no authorization secret | Exact provider generation intent including frozen transport URLs |
| `request_sha256` | Canonical provider request JSON | Stored outbound body integrity |

Canonicalization uses [RFC8785](https://www.rfc-editor.org/rfc/rfc8785). Reject non-finite numbers, duplicate keys, invalid Unicode and integers outside JavaScript's exact integer range. Do not normalize source copy or silently trim strings. All money is integer USD microdollars, never binary floating-point dollars. Parent and child receipt hashes are independent: omit only the current object's own `sha256`, not nested receipt digests.

Semantic projections are fixed for v1:

- Request: `schema_version`, origin/source bindings, brand package, offering, audiences, objective, hook, locale, exact copy, formats, references, provider selection and constraints. Exclude the entire `origin.generation_eligibility` receipt, which is an admission observation rather than creative content. Funding credentials and approval-check timestamps are admission data; the plan carries the budget ceiling explicitly.
- Treatment: `schema_version`, `direction`, `shots`, `sources`. Acceptance and authorship receipts remain source provenance and eligibility checks.
- Recipe graph: `schema_version`, `template`, `nodes`, `outputs`. Before hashing, replace node config composition VersionRefs with `{kind:"composition",semantic_sha256:<composition projection hash>}`. `authoring_view`, root identity/audit fields, and references to separately hashed request/treatment/composition are excluded. Lowered render parameters contain concrete resolved layout values and that semantic hash, not an ancestry-sensitive composition pointer; exact source VersionRefs stay in plan provenance.
- Composition: `schema_version`, `kind`, `artboards`, `video`. `basis_recipe`/`basis_run` remain provenance; excluding them from semantics prevents irrelevant ancestry changes from altering render work.

Normalize unordered collections before deriving semantic projections: recipe nodes by stable ID, node inputs by `(port, ordinal)`, output declarations and reference collections by stable ID, artboards by ID, layers by `(order, id)`, shots by `(sequence_start_frame, id)`, caption/overlay/audio entries by `(start or offset frame, id)`. Preserve semantic arrays such as ordered provider references and sequences of prompt fragments. Lower program nodes in topological order with stable-ID tie breaking; dependencies are sorted. Catalogue and compiler revisions, effective model/parameters, prompt text, source hashes, render versions and referenced file hashes are included. Expiring URLs, filesystem paths, provider request IDs, runtime progress and wall-clock timestamps are excluded from `program`.

The same program has the same compile hash. It does **not** promise identical generated pixels or video, even with a seed. Each actual provider output gets its own byte hash and asset version. A rerun is a new execution with explicit reuse of selected earlier outputs. Existing Python sorted-JSON hashes remain `legacy_compile_hash`; they are not relabeled as RFC8785 hashes.

## 4. Stable assets, rights and refresh

`AssetRef = {authority, workspace_id, asset_id, asset_version_id, revision, sha256, mime_type}`. A signed URL, local path or provider media ID is transport, never identity. A file-changing edit creates a new `asset_version_id`; cropping or rasterizing an SVG is a derivative with `parent_assets` pointing to the original. Original logo/font/media bytes are retained. Logical asset identity may group versions, but hashes never select another workspace's file.

At import, Studio requests a fresh download ticket from the asset owner, checks its exact scope/version/hash, streams with a size limit, verifies the received MIME and byte hash, and writes an immutable Studio-owned copy. The source AssetRef and rights receipt remain attached to that copy. A unique `(Studio workspace, source authority, source workspace, source asset version, source byte hash)` mapping reuses the same mirror on replay. No asset is marked available until persistence and metadata commit are complete. Private paths follow `<workspace_id>/assets/<asset_id>/<asset_version_id>/<sha256>.<ext>`; path names never grant access.

Download tickets are separate responses with URL and expiry. Renewing a ticket for unchanged bytes does not create a new asset or plan. A missing source becomes `source_unavailable`; changed bytes under the old ID become `asset_hash_mismatch`, never an automatic new version. Before initial provider dispatch, tickets must remain valid for the catalogue's required fetch window. After dispatch begins, the exact provider request including URLs is frozen for same-intent reconciliation. An expired URL cannot be silently substituted into an idempotent replay: first reconcile the old intent; a changed request requires an explicit new intent/admission. Successfully mirrored source files remain reusable when their original website URL expires, subject to current permission.

Rights are independent versioned evidence. `unknown` is not permission. `permitted`/`restricted` must cover the requested operation, channel, territory, validity period and redistribution/embedding as applicable. Empty rights scope grants nothing; an explicit `*` denotes all channels/territories only when supported by the actual evidence. `denied` refuses use. Public website access and guide approval do not establish font, image, trademark or redistribution rights. Unresolved decisions can be preserved in a package, but a blocking decision affecting the current operation refuses that operation. A withdrawal creates a new rights/revocation event; historical bytes and provenance stay readable under retention policy while new restricted actions stop.

## 5. Approved brand package and action-specific eligibility

Brand CI exports one exact `bci_version_id`, its original digest and declared digest algorithm, its canonical document digest, original document creator, current approved receipt, approved PDF, and all referenced file records. The package is a read-only derivative of that guide. Studio never creates another editable guide authority. No package may silently resolve “latest” after the request pins a revision.

The package contains actual logo variants and usage constraints, palette, font bytes, imagery/voice rules, prohibited uses, unresolved decisions and rights. Every logo/font/media reference must be present in `assets` with matching hash and scope. Typography without a licensed usable font asset is a visible `font_asset_missing` blocker for exact export, not a silent substitute. An explicitly accepted substitution creates a new request/composition revision. A legacy BCI asset can be exported as revision 1 with `asset_version_id = asset_id`; it must retain that identity/hash immutably thereafter.

Current BCI code records owner approval. Export it honestly as `bci-owner-approval-v1`; do not invent an independent-review claim. MOS can map compatible guide/version/receipt evidence into its existing policy and retain a derived brand-rule pointer. It must not demand a duplicate guide as the default integration. Where existing MOS policy requires an independent actor, compare the original author and receipt actor or collect the missing review under MOS's own action; never fabricate equivalence.

Three different actions are intentional:

1. Draft authoring and preview may display draft/unresolved upstream evidence and compile diagnostics. They cannot return a runnable successful plan that bypasses a failed prerequisite.
2. Paid generation requires an approved Brand CI package, accepted treatment, sufficient rights, budget and current action-specific generation eligibility. MOS verifies its approved concept and the source revisions required by its current policy. Optional MOS brand-rule mapping evidence is required only when that generation policy requires it.
3. Official delivery and publication require their own current checks. A Studio export is not MOS approval. MOS keeps policy/brand/clinical/client review, version binding, destination and claim gates. Missing independent review is displayed at the action whose policy requires it.

Source policy checks are API receipts, not body assertions. Client-supplied receipts are verified with their issuer; admission checks have at most a 60-second freshness window. A new upstream current revision does not mutate historical work. It marks new actions stale where policy requires current versions, offers an explicit rebase, and invalidates the applicable MOS approval through MOS's own rules.

## 6. Source request and frozen treatment

The request pins brand, offering/service-group revision, one or more audience/avatar revisions, approved concept, exact copy and its claim set, objective/hook, locale, references, output formats and budget policy. MOS origin preserves campaign/placement/destination/policy references; standalone origin has its own direction approval. Claimed facts remain source-linked, never promoted from generated text.

`offering.kind` is `physical_product`, `service`, `service_group`, `digital_product` or `hybrid`. `physical_product_spec` is optional for physical offerings and must be null for service/service-group. Services use `summary`, value propositions and sourced service facts. The compiler's new lane emits service context without synthetic CAD, imaginary geometry, product-preservation flags or empty placeholder geometry. Existing ProductSpec v2 remains a physical compatibility lane.

AI treatment drafting is a separate explicit, metered authoring action. Admission creates a durable `treatment_authoring` operation before a treatment, recipe, plan or run exists. Save its immutable funding admission, attempt/receipt IDs, raw-response hash, model and prompt-template revision; the user can edit the resulting direction. Success creates only a draft treatment linked to its operation and attempt. Acceptance is a separate user action that freezes the treatment revision before compilation. Compile, refresh, resume and rerun never call a model to rewrite direction. A text/model/template change creates a new treatment/recipe revision and visible plan/cost changes.

Exact logos, claims, captions, CTA and copy are composition layers. Generative prompts may request a scene with space for overlays; they do not make provider-redrawn marks or text authoritative. MOS copy editing creates/returns the appropriate MOS copy revision through its own API before final handoff; the request and composition then pin that revision. A changed visible/spoken string without that source revision is an explicit draft mismatch, not an approved export.

## 7. Shared recipe, typed nodes and partial reruns

Guided builder and canvas submit commands to the same revision writer with `If-Match` on the saved artifact hash. `409 revision_conflict` returns the current reference; neither view overwrites the other's unsaved changes. Canvas positions and collapsed nodes are presentation metadata. They do not change `compile_hash`, incur spend or rewrite provider parameters.

An initial empty Studio session saves as `cs-authoring-draft-v1`, a non-executable workspace record with optimistic revision/hash, selections, pending form values and working nodes. It is not another compiler language. Its ID is refused by compile/run/export. Promotion is an explicit atomic operation after required source refs exist, treatment is accepted, and the proposed canonical recipe passes structural/semantic checks. Promotion saves the ordinary immutable recipe and records its reference in the draft; repeating the same admission returns that recipe. An incomplete draft can always be left and resumed without weakening canonical recipe requirements.

Each recipe node has stable `id`, registered `kind`, typed config and named ordered input bindings. V1 kinds are `asset.load`, `image.generate`, `video.generate`, `image.edit`, `image.transform`, `image.compose`, `video.assemble`, `export.render`. These are source node kinds; the existing graph compiler lowers them to execution-only nodes. Every kind requires a registered config/port schema and versioned implementation. Unknown kinds/fields/ports, multiple values for a singleton input, duplicate IDs or `(port, ordinal)` pairs, dangling edges, cycles and unreachable required outputs fail compilation. No arbitrary Python, shell, endpoint or custom executable node is accepted.

| Kind | Inputs | Outputs | Required semantic binding |
|---|---|---|---|
| `asset.load` | None | `media` | Exact available AssetRef |
| `image.generate` | Ordered optional `reference` | `image` | Image format and selected/auto catalogue policy |
| `video.generate` | Catalogue-supported `reference`, `start_frame`, `end_frame`, `audio` | `clip` | Accepted shot and <=10-second clip requirement |
| `image.edit` | `image`; optional `mask` and references | `image` | Supported operation and exact source/mask versions |
| `image.transform` | `image` | `image` | Crop/resize or supported upscale with explicit cost policy |
| `image.compose` | `media` bindings used by the pinned artboard | `image` | Composition/artboard and exact fonts/logo/copy |
| `video.assemble` | Exact clip/audio/image bindings | `video` | Pinned composition timeline |
| `export.render` | `image` or `video` matching format | `export` | Pinned composition and final format |

Compilation resolves the effective model from one immutable catalogue. Auto selection filters all requested capabilities and rights/funding availability, then chooses the least estimated total cost; ties use catalogue entry ID. Selected mode either resolves that exact model or refuses. It never silently substitutes another model. Request parameters must validate against that entry's frozen JSON Schema. UI controls consume that same revision.

Generation-only recipes can produce durable image/clip assets before composition exists. Adding a composition creates it against an earlier `basis_recipe` and optional run snapshot, then creates a new recipe pointing to it; no recipe/composition hash cycle. Export recipes bind all chosen assets. A saved template is copied into an ordinary recipe revision with template provenance; later template edits cannot alter saved work.

A partial rerun creates a child run or a new plan when semantics changed. Compute affected descendants, show their estimated/maximum costs, and require explicit admission. Reuse only exact successful output AssetRefs with matching node-input hash and current rights. The parent run and original outputs never change. Reused outputs still retain original cost and attempt lineage; shared attempt costs are counted once. Unchanged downstream composition can be rerendered without another media generation. `skipped` is valid only for a pruned node, never a required output hidden by failure.

## 8. Frozen execution and provider transport

`program` contains concrete prompt strings, effective models, endpoint keys, parameter values, typed input/output slots, implementation versions and per-node costs. It contains no draft source languages, secrets, URLs or local paths. Runtime consumes the plan and verifies its hash. It does not compile, choose a model or infer a prompt.

For an upstream node output, the program binds a node/port/ordinal; actual bytes cannot exist yet. Runtime resolves that exact successful output to an AssetRef, records `node_input_sha256`, binds validated media transport, freezes the outbound request, then records an attempt before dispatch. Temporary provider media IDs/URLs and receipts remain protected runtime records. They do not alter the program. Downloads and status destinations use configured provider origins and verified paths/request IDs; arbitrary persisted URLs never receive provider credentials.

The present Higgsfield API documents same-account generation replay using an `Idempotency-Key` with unchanged endpoint, JSON, webhook and expiry header. Its response supplies the request ID and status URL. A mismatch is a refusal, not a reason to rotate the key. Studio permits at most three identical reconciliation replays when the frozen catalogue proves support; otherwise dispatch uncertainty stops for reconciliation. A new generation always requires a new admitted intent. [Higgsfield idempotency](https://docs.higgsfield.ai/docs/concepts/idempotency).

Unsigned callbacks cannot finalize work. Validate raw-body signatures and freshness, scope to the saved provider account/request, persist event identity before acknowledgement, and deduplicate. Where the account has no configured signing secret, use authenticated polling. [Higgsfield webhooks](https://docs.higgsfield.ai/docs/how-to/webhooks).

## 9. Durable run, attempts and money

Compilation, package import and AI treatment authoring use `cs-operation-v1` before a runnable plan exists. Atomic admission records its scoped ID, kind, immutable request hash/input references, API idempotency binding, initial event and durable work item; HTTP 202 returns that operation with `Location: W/operations/{id}`. `cs_admit_compile` accepts only the exact immutable recipe and catalogue refs, never a draft ID. The compile worker resolves/checks inputs and persists the plan before committing `completed` plus its result VersionRef. Failed compilation stores diagnostics on the operation and creates no runnable plan. Import completes only after the exact package and mirrored assets are durable; authoring completes with a draft treatment only. Request-time authentication/structural refusal still returns 4xx without admitting an operation.

Operations use the same state names as runs but their own event sequence; they are not runs and cannot be passed to the execution API. Result references remain empty until committed results exist. Every work item, attempt, budget reservation, provider receipt and generated-asset provenance has exactly one parent: `(workspace_id, run_id, node_id)` or `(workspace_id, operation_id, operation_step_key)`. An exactly-one-parent constraint and scoped foreign keys enforce this. A treatment operation uses the same frozen dispatch envelope, immutable budget reservation/admission, provider account binding, attempt numbering, billing receipts and uncertain-cost rules as media execution, without creating a fake accepted treatment, recipe, plan or run. Treatment `authorship.operation_id` and `attempt_id` retain that cost/provenance lineage after acceptance. Compile/import do not call paid models and have null paid admission/cost fields.

Run states: `queued`, `running`, `waiting_provider`, `needs_reconciliation`, `budget_blocked`, `authorization_blocked`, `cancel_requested`, `completed`, `failed`, `cancelled`. Node states add `pending`, `succeeded`, `skipped`; attempts use `prepared`, `dispatching`, `submitted`, `waiting_provider`, `ingesting`, `succeeded`, `failed`, `uncertain`, `cancel_requested`, `cancelled`.

| Transition | Transactional precondition/effect |
|---|---|
| Request → queued run | Unique operation idempotency key, exact request hash, valid plan/eligibility, atomic budget reservation and durable work item |
| Queued node → prepared attempt | Dependencies persisted, immutable node-input hash, reserved maximum spend, unique attempt number |
| Prepared → dispatching | Durable frozen dispatch envelope and provider idempotency key written before network call |
| Dispatching → submitted/waiting | Save returned provider request ID once; queue a later status step; release worker invocation |
| Ambiguous dispatch → uncertain | Keep reservation/exposure; use allowed identical replay or mark `needs_reconciliation`; never assume no charge |
| Provider success → ingesting → succeeded | Persist all required original outputs, verify hashes/metadata, then atomically expose assets and descendants |
| Definite failure | Store classified error and billing evidence; preserve partial outputs; a deliberate new attempt is a new admission |
| Cancel request | Prevent new paid dispatch; request provider cancellation if supported; continue reconciling in-flight cost/results |
| All required outputs durable → completed | Commit terminal run event; costs may still explicitly be `unknown` pending a later billing receipt |

Internal work items use leases, monotonically increasing fencing tokens and expected state transitions. A stale worker cannot commit over a newer claim. A crash before dispatch can be reclaimed; a crash in dispatch is uncertain until provider evidence resolves it. No in-memory task registry or browser session owns completion. Per-run event sequence is monotonically increasing; event append and state projection are one transaction. Polling/SSE are read-only views, resumable by sequence.

Money fields: `estimated_micros` is a quote; `reserved_micros` is held budget; `settled_micros` is evidenced charge; `unresolved_micros` is maximum remaining exposure. `reserved` means not settled, `unknown` means ambiguous exposure remains, `settled` means none remains. Releasing unused reservation requires positive evidence of no charge, a final charge amount, or an explicit documented operator reconciliation. A failed status alone does not mean free. `settled_micros + unresolved_micros` may not exceed the admitted maximum without recording a provider billing exception and blocking new spend. A funding renewal cannot increase the frozen plan ceiling; increasing it requires a new plan/admission. Polling, download retry and render retries do not start a new paid generation.

Run idempotency is `(workspace_id, operation, idempotency_key)` with canonical admitted request hash. Same key/same body returns the same resource even after response loss; changed body returns `409 idempotency_conflict`. Retain the binding for the resource's retention lifetime; expiration never silently admits a duplicate. Provider idempotency is a different per-attempt key tied to the provider account, exact endpoint and dispatch envelope. Receipts cannot be moved between workspace credentials.

## 10. Image and video composition

Image export presets are exactly 1080×1080, 1080×1350 and 1080×1920. The platform's longest-edge 4096 limit is an input/normalization safety bound, not an additional export preset. PNG/JPEG/WebP exports use sRGB; JPEG requires a resolved opaque background. All layer IDs and ordering are unique within an artboard; group references stay in that artboard and form an acyclic single-parent hierarchy. Transforms are in artboard pixels; crop coordinates are normalized and must remain within the source (`x + width <= 1`, `y + height <= 1`). Logo aspect ratio, minimum size and clear space are checked against the pinned package. Exact font bytes, glyph coverage, weight, layout engine and render version are recorded. No silent font substitution, text truncation or out-of-bounds required copy.

Video is a bounded campaign editor: at most 30 seconds / 900 frames, six clips, ten seconds per clip, 1080×1920 at 30 fps. Source clips normalize to a consistent pinned video/audio profile before assembly. Each source interval is valid and within the asset duration. Normalized length is `round_half_up((source_out_frame - source_in_frame) * 30 * source_fps.den / source_fps.num)`, at most 300 frames. Time spans are half-open `[start, end)` with `start < end`; no frame can fall outside `duration_frames`. First shot starts at frame zero. Cut clips abut; a crossfade is 1–30 frames and makes `next.start = previous.end - overlap`; incoming plus outgoing overlap is shorter than the interior shot. Timeline duration equals the last output frame, with no accidental gap or undisclosed overlap. Captions, overlays and audio use this same frame clock. Audio trim values are timeline-rate frames converted to 48 kHz samples; resampling/encoder settings are part of the render implementation version. Group children use local coordinates; a group resize saves scaled children rather than an implicit second scale.

Title/end cards are ordinary exact layers over a still clip or artboard. Replace one clip by selecting a new source version and saving a new composition; unchanged clips are reused. Per-stage media input is limited to 45 MB, shot normalization work is chunked to at most five seconds, and assembly is a separate bounded worker stage. A crossfade renders the outgoing tail/incoming head as a <=1-second transition clip, trims those frames from body segments, and joins normalized body/transition segments in a final stream-copy stage. Typography uses the pinned platform renderer with explicitly supplied approved font files and system fonts disabled; static layers, captions and cards share those pixels. These are chosen build limits, not measured performance promises. Hosted execution time and quality proof belong to CS-M3.

## 11. Export and MOS ingestion

An export manifest is issued only after the final file is durably stored, byte-hashed, MIME/dimensions/duration read back, and deterministic render constraints pass in the built product. It binds request, treatment, recipe, composition, plan/compile hash, brand package, catalogue, source output attempts, fonts, rights and exact copy. The architecture describes these future runtime checks; none ran in CS-M1.

Handoff uses immutable export references, not provider CDN links. MOS fetches the manifest and renewed file ticket from Studio, verifies workspace mapping and current action eligibility, downloads with a limit, checks the declared byte hash and persists an owned copy or its own durable storage binding. MOS uses its own `mos_ingest_asset_version` evolution and rights/version APIs. The existing handler only records source URL/hash and is not yet this complete durable import.

MOS adds a unique source binding `(mos_workspace_id, studio_export_version_id, studio_export_sha256)` and handoff idempotency mapping. An identical retry returns the same asset version/receipt. A conflicting hash under one version is refused. Record cost/lineage/rights and current copy/concept references, then create the ingestion receipt. Partial transfer preserves successful imports but does not emit a complete receipt. Complete receipts start in `pending` review and `publication_eligible = false`.

Brand CI approval, local gallery approval and Studio export success cannot populate a MOS content-approval row. MOS's review binds its exact imported asset/copy/claim/destination/policy versions; changing any bound source invokes MOS's own invalidation rules. Existing NS-10631 owns this integration; no competing MOS compiler/task is introduced.

## 12. Compatibility and failure behavior

See [endpoint-outline.md](endpoint-outline.md) for bodies, status codes and optimistic concurrency, and [compatibility.md](compatibility.md) for exact existing code symbols and required changes. Unknown schema versions return `unsupported_schema_version`; never ignore new fields or strip them into an older schema. V1 and v2 compiler readers remain unchanged compatibility paths until explicitly adapted; there is no dual write to the nested historical copy.

Only a successful complete structural and semantic compile returns a runnable immutable plan. Failed compilation returns diagnostics and makes no provider call. Schema files, source code, deployment success and model catalogue presence are not acceptance evidence. The full architecture → hosted build → testing sequence remains binding.
