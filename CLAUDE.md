# image-pipeline — project context

## What this is
Deterministic creative compiler + operator UI. `job-v1` JobSpec in, versioned
image out. Core compiler (`core/`) is provider- and tenant-agnostic — it asks
an adapter registry by name, never hardcodes a tenant or backend. Consumer
tenants (e.g. `tenants/joga/tools/image-pipeline/`) hold only data: JobSpecs,
`cad.md`, reference images. No engine code lives in a tenant repo.

Full architecture docs: `SKILL.md` (agent playbook — read this first),
`README.md`, `ADAPTERS.md` (frozen adapter contracts), `WORKFLOW.md` (node
contract), `docs/COMPILER_FOUNDATIONS.md`, `docs/ENTERPRISE_GREENFIELD_MVP.md`.

## Tech stack
- Core: bare Python 3.9+ (dep-free by design), `core/run_job.py` /
  `core/run_graph.py` / `core/compile_job.py`.
- UI backend: FastAPI, `ui/server.py`, needs Python **3.10+** (claude-agent-sdk
  requirement) — `ui/dev.sh` auto-picks a modern interpreter and rebuilds
  `ui/.venv` if it's on <3.10.
- UI frontend: React + Vite + TypeScript, `ui/app/`.
- AI prompt drafting (`ui/brief_drafter.py`): `claude_agent_sdk.query()` —
  drives the locally-authenticated `claude` CLI (subscription auth, same as
  `services/sdk-executor`), **not** a billed API key. No `ANTHROPIC_API_KEY`
  anywhere in this repo on purpose.

## Current state (as of 2026-07-08)
Verified working end-to-end for any tenant with a real job-v1 structure
(currently: **joga** only — fireforge/regenic-lab are pre-compiler legacy
layout, no `jobs/` dir, correctly excluded from tenant auto-discovery):

- Tenant selector (topbar) scopes products/campaigns/artifacts/knowledge
  storage per tenant (`ui/tenants.py`, `config/tenants.json`).
- Product browsing works for both knowledge-store products (ProductSpec-v2)
  and jobs_only products (disk-scanned, no spec) — `GET /api/products/{slug}`
  unifies both.
- Artifact gallery: paginated, grouped by shot (collapses `-v2`/`-v3` version
  iterations), filterable by pending/approved/rejected, real approve/reject/
  comment actions, per-tenant isolated storage
  (`workspace/<tenant>/artifact_registry.json`).
- Job brief modal (`ui/app/src/components/BriefModal.tsx`): describe a shot →
  AI drafts `prompt_template` (using the product's `cad.md` + its own sibling
  prompts + `SKILL.md` as system context) → edit → pick references → save →
  dry-run → generate. Two entry points: "+ New shot" on a product page,
  "Regenerate…" on any artifact card (pre-fills AI context with that card's
  rejection reason).
- Visual design: Linear/Notion-precision pass — bordered surfaces (no
  blur-glass), one warm-brass accent, Fraunces/Manrope/JetBrains Mono.

## Known gaps — ranked by impact
1. **New-product onboarding has no UI path.** The brief modal needs an
   existing job to read `cad.md` + house-style examples + engine defaults
   from — it 404s on a product with zero jobs. First CAD extraction for a
   genuinely new product is still CLI-only (`core/spec_extractor.py` exists
   but nothing in the UI calls it). This is the single biggest blocker to
   "use the UI for everything, start to finish."
2. **The two-stage plate recipe isn't guided anywhere in the UI.** SKILL.md
   calls this "the core skill" for staying on-model (plates → curate →
   promote to `reference/plates/` → record in `cad.md §9` → scenario shots
   reference the plates as multi-ref). A user can skip straight to scenario
   shots via the brief modal and get inconsistent renders with no warning.
3. **Output delivery is local-only.** `output_local.py` writes PNGs to
   `out/`; approving an image in the gallery doesn't push anywhere. No
   Shopify CDN / Google Drive / webhook adapter exists yet (tracked
   elsewhere as #29839 — status unconfirmed, may be dormant; check before
   assuming it's in progress).
4. **Two parallel generate paths.** The pre-existing `/generate` page
   (knowledge-store + campaign based) and the new brief modal both create
   and run jobs, from different data models. Not reconciled — worth an
   explicit architecture decision on whether they converge.
5. **No tenant management UI.** Adding/editing a tenant is a raw
   `POST /api/tenants` call; `/settings` is still a stub page.
6. **Planning/roadmap layer is still a hand-maintained markdown file**
   (`TRACKER.md` in each tenant's job root) — X products, Y shots each,
   have/need/blocked — fully disconnected from the runs registry / artifact
   gallery. Explicitly out of scope this session (per user: fix the compiler
   before more planning tooling), but a real gap for "how do I know what's
   left to shoot."

## Gotchas learned this session
- `discover_images()` in `ui/artifact_registry.py` must scan recursively —
  tenants can nest products under a campaign folder
  (`jobs/<campaign>/<product>/out/`), not just flat `jobs/<product>/out/`.
- Two reference-folder conventions coexist across tenants: this repo's own
  jobs use `reference/`, joga's real production jobs use `refs/` — any code
  that lists reference images must check both.
- `_product_from_job_path` should key off the job file's own parent
  directory, not a fixed offset from a literal `"jobs"` path segment —
  breaks for any tenant whose consumer workspace lives outside this repo.
- Bash on this machine mangles multiline heredocs/command chains
  intermittently — prefer single-line commands or Write a script file.

## Manual Executor (BRIEF one-shot plane)

This repo includes the **manual sdk-executor package**. Agents MUST use it for autonomous BRIEF execution — do not hand-assemble env blocks.

| Half | Command |
|------|---------|
| Exec | `./scripts/run-brief.sh --brief-id N` |
| Verify | `./scripts/verify-brief.sh --brief-id N` |
| Preflight | `./scripts/run-brief.sh --brief-id N --dry-run-only` |

- Tenant: **north-star** (`00000000-0000-0000-0000-000000000001`)  
- Default lane: **deepseek**  
- Manifest: `.manual-executor/manifest.json`  
- Full contract: `docs/MANUAL_EXECUTOR.md`  

Programme work: frontier task → scribe brief → dry-run → exec → verify → close task row.

<!-- manual-executor-v1 -->

## Git Oracle Pipeline
<!-- git-oracle-pipeline-v1 -->

Sequential **exec → land gate → verify** chain for programme work. Do not call
`sdk_executor.py` directly — use repo delegates.

| Action | Command |
|--------|---------|
| One brief (exec+verify) | `./scripts/brief-oracle-step.sh --brief-id N` |
| Sequential chain | `./scripts/chain_runner.sh ID [ID...]` |
| Exec only | `./scripts/run-brief.sh --brief-id N` |
| Verify only | `./scripts/verify-brief.sh --brief-id N` |

- Tenant: **north-star** (`00000000-0000-0000-0000-000000000001`)
- Default branch: **main** · Push mode: **pr**
- Manifest: `.git-oracle-pipeline/manifest.json`
- Full contract: `docs/LANDING.md`

<!-- git-oracle-pipeline-v1 -->
