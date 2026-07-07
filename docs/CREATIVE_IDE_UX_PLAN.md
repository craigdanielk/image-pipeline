# Creative IDE — UX Plan (audit-scored)

**Product:** Creative Compiler operator surface (`services/image-pipeline/ui`)  
**Mental model:** Visual Studio Code for Creative Compilation — **not** Weave / ComfyUI  
**Design system:** FractalOS **ARAGON** surface — dark glass over living context, indigo accent  
**Audit framework:** Control Tower `ui-ux-architecture-audit` (L1–L10)  
**Date:** 2026-07-07

---

## One-line brief

> Build the world's first Creative IDE: structured knowledge replaces prompt engineering, compilation replaces orchestration, every asset is deterministic and inspectable — with **floating glass panels** over a living context layer.

---

## Audit baseline (current UI vs target)

Scored against **UI/UX Architectural Audit Framework v1.0** (0–5 per layer, 10% weight each → 0–100 overall).  
Current = post–Weave wizard single-page app. Target = Creative IDE spec + FractalOS glass.

| Layer | Focus | Current | Target | Gap |
|-------|--------|---------|--------|-----|
| **L1** | Strategic UX alignment | 2 | 4 | Dev-tool framing; no creator value prop on Dashboard |
| **L2** | Information architecture | 2 | 5 | Missing Products/Campaigns/Runs nav; paths as labels |
| **L3** | Interaction architecture | 3 | 4 | Compile/run feedback OK; **prompt box = violation** |
| **L4** | Visual systems | 3 | 5 | Flat panels; not three-layer glass floating |
| **L5** | Accessibility | 3 | 4 | Focus/contrast TBD; audit each phase |
| **L6** | Frontend engineering | 4 | 5 | React+Vite sound; needs route-based IA |
| **L7** | Performance | 4 | 4 | SPA weight OK; lazy-load Runs graph |
| **L8** | Conversion / task completion | 2 | 4 | Generate buried; no cost-before-commit on primary CTA |
| **L9** | Design system integrity | 2 | 5 | Partial tokens; glass rules not enforced |
| **L10** | Behavioural friction | 2 | 4 | JSON + graph + jobs same plane = cognitive overload |

| | Score |
|---|------|
| **Current overall** | **~37/100** (risk: **high**) |
| **Target overall** | **~84/100** (risk: **low**) |
| **Minimum ship bar** | **≥72/100**, no L2/L9 below 4, zero critical violations |

### Critical violations to close (P0)

| ID | Layer | Violation | Principle |
|----|-------|-----------|-----------|
| V1 | L2 | Single-page job path picker | First-class objects must own navigation |
| V2 | L3/L10 | Prompt textarea in creator flow | **Zero prompt boxes** — compiler owns prompts |
| V3 | L9 | Opaque panels, no `backdrop-filter` | FractalOS glass spec |
| V4 | L1/L8 | Generate not primary creator action | Generate is the killer screen, not a side panel |
| V5 | L4 | No context layer beneath UI | Glass over void is meaningless |

---

## Design principles (audit + FractalOS)

### Core audit principle

**Reduce cognitive load while increasing confidence and task completion efficiency.**

Applied to Creative IDE:

- **Creators** see: Product → Campaign → Outputs → Changes → Generate (5 decisions max)
- **Developers** opt into: Runs, Pipelines, Providers, raw JSON (progressive disclosure)
- **Never** show `prompt_template` in creator mode — only compiled preview in Runs

### Three-layer depth model (mandatory)

```
LAYER 3 — ACCENT     indigo active nav, status dots, Run CTA, connector glow
LAYER 2 — GLASS      floating nav, cards, inspector sheets, modals
LAYER 1 — CONTEXT    latest approved hero / dot-grid pipeline canvas / subtle mesh
```

**Glass floating pieces:** panels do not span edge-to-edge. Use outer padding (`24–32px`), `gap` between floats, `border-radius: 20–28px`, `backdrop-filter: blur(24px)`, `1px` glass border. Context layer always visible in gutters or full-bleed behind glass.

### ARAGON tokens (Creative Compiler)

```css
--color-accent: #6366F1;
--color-accent-bright: #818CF8;
--color-glass-neutral: rgba(255, 255, 255, 0.04);
--color-glass-active: rgba(99, 102, 241, 0.12);
--color-glass-border: rgba(255, 255, 255, 0.08);
--color-glass-border-active: rgba(99, 102, 241, 0.35);
```

Accent signals — never fills entire surfaces except active Run pill / selected graph node (Runs dev view only).

### What we steal from Weave (debug surfaces only)

- Image-in-card previews (Assets, Generate result)
- Credit estimate before Run (Generate sticky bar)
- Left icon rail (global nav)
- Dot-grid canvas (Pipelines / Run graph viewer only)

### What we reject as primary UX

- Prompt nodes, manual wiring, model-per-node picker, infinite canvas as home

---

## Information architecture

### First-class objects (navigation source of truth)

```text
Workspace
 ├── Products      (PIM — ProductSpec)
 ├── Campaigns     (CreativeSpec)
 ├── Assets        (references + outputs + lineage)
 ├── Generate      (creator killer screen — one route)
 ├── Runs          (DevTools — 1 row per execution)
 └── Pipelines     (read-only compile chain — dev only)
```

Secondary (dev / admin): Dashboard, Knowledge, Providers, Settings

### Route map (React Router)

| Route | Mode | Purpose |
|-------|------|---------|
| `/` | All | Dashboard |
| `/products` | Creator | Product list |
| `/products/:slug` | Creator | Product explorer (geometry, logos, constraints, versions) |
| `/campaigns` | Creator | Campaign list |
| `/campaigns/:id` | Creator | Mood, lighting, camera, palette, scene locks |
| `/generate` | Creator | **Primary CTA surface** |
| `/assets` | Creator | Relationship browser |
| `/assets/:id` | Creator | Lineage drill-down |
| `/runs` | Dev+ | Run list |
| `/runs/:id` | Dev+ | Compile chain, graph, logs, replay, diff |
| `/pipelines` | Dev | Read-only compiler DAG |
| `/knowledge` | Dev | Package browser |
| `/providers` | Dev | Capability registry |

**Creator mode default:** hide `/pipelines`, `/providers`, `/knowledge`, raw paths. Toggle: **Developer mode**.

---

## Generate screen (killer UX — zero prompts)

```
┌─ glass float ─────────────────────────────────────────────┐
│ Product    [ Hairband ▾ ]    Campaign  [ Starz Kid ▾ ]   │
│ Outputs    [x] PDP  [x] Hero  [ ] Lifestyle  [ ] Social │
│ Changes    [+ Replace Logo] [+ Subject Swap] [+ Remove BG]│
│ Est. cost  ~2 credits          [ Estimate ] [ Generate ]  │
└───────────────────────────────────────────────────────────┘
```

**Compiler chain (invisible to user):**

```
knowledge/products/{slug} + knowledge/campaigns/{id}
  + operations/{op}[]  →  EditSpec v2[]
  → compile/  →  graph  →  execute  →  Assets
```

No `prompt_template` field in UI. Changes map to `knowledge/operations/*` enums.

---

## Glass component library (build before screens)

| Component | Layer | Spec |
|-----------|-------|------|
| `GlassShell` | 2 | App chrome; context bleed in margins |
| `GlassNavRail` | 2 | 64px icon rail + labels on expand |
| `GlassCard` | 2 | `blur(24px)`, radius 20px, 1px border |
| `GlassCard--active` | 2+3 | indigo border + ambient glow |
| `GlassModal` | 2 | `blur(40px)`, `rgba(10,10,10,0.92)` |
| `GlassSheet` | 2 | Right inspector; floats over context |
| `ContextCanvas` | 1 | Dot-grid or latest hero `object-fit: cover` at 15% opacity |
| `AccentPill` | 3 | Credits, status, selected output type |
| `RunBar` | 2+3 | Sticky glass footer on Generate |

**Rule:** every screen has visible Layer 1 in at least 20% of viewport (gutter or background).

---

## Cloud data model (creator source of truth)

| Entity | Store | Maps to |
|--------|-------|---------|
| Product | `products` + `cad_specs` + Supabase Storage | ProductSpec v2 |
| Campaign | **new** `campaigns` table | CreativeSpec v2 |
| Run | `jobs` + `graph_runs` + `compile_artifacts` | compile/ manifest |
| Asset | `reference_images` + `job_outputs` | lineage FKs |
| Knowledge | `knowledge/` git + optional `documents` sync | compiler libraries |

Filesystem `jobs/*.job.json` → **export mirror** for JOGA git workflows, not creator primary.

---

## Phased delivery

Each phase ends with **audit gate** (see below).

### Phase 0 — Design foundation (1 sprint)

**Deliverables**

- `ui/app/src/design/` — FractalOS ARAGON tokens + glass primitives
- `ContextCanvas` + `GlassShell` layout
- React Router skeleton; all nav routes stubbed
- Remove prompt textarea from creator paths (feature flag old wizard off)

**Acceptance**

- [ ] Three-layer depth visible on Dashboard stub
- [ ] L9 score ≥ 3 on `/` (tokens + glass-card present in DOM)
- [ ] No `textarea` with label "Prompt" in creator routes

### Phase 1 — IA shell + Dashboard (1 sprint)

**Deliverables**

- Dashboard: stats cards (products, campaigns, runs, credits), recent runs, quick actions
- Left `GlassNavRail` with full nav list
- Creator/Developer mode toggle

**Acceptance**

- [ ] L2 ≥ 4 — all primary nav items reachable in ≤2 clicks
- [ ] L1 ≥ 3 — Dashboard states Creative IDE value prop in &lt;120 chars hero

### Phase 2 — Products + Campaigns PIM (2 sprints)

**Deliverables**

- Products CRUD UI → `knowledge/products` + Supabase `products`/`cad_specs`
- Campaigns CRUD UI → `knowledge/campaigns` + new `campaigns` table
- Structured forms only: mood, lighting, camera, palette, scene_locks — **no prose prompts**
- Product Explorer tree (VS Code outline style)

**Acceptance**

- [ ] Hairbands golden path authored entirely in UI
- [ ] L10 ≥ 4 on `/products/:slug` — max 3 type sizes per card, no JSON default view
- [ ] V2 closed — zero prompt boxes on Products/Campaigns

### Phase 3 — Generate + cloud Runs (2 sprints)

**Deliverables**

- Generate screen (spec above)
- `POST /api/runs/create` — product_id + campaign_id + output_types + changes[] → compile → queue
- Credit estimate before Generate (dry-run)
- Runs list + detail (compile chain viewer); graph on **Runs only**, not home

**Acceptance**

- [ ] End-to-end: Generate hairbands logo-fix without touching JSON
- [ ] L8 ≥ 4 — primary CTA visible without scroll on 1440×900
- [ ] L3 ≥ 4 — loading/success/error on Generate + cost displayed before commit

### Phase 4 — Assets + lineage (1 sprint)

**Deliverables**

- Assets grid with glass cards + image previews
- Lineage panel: Asset → Run → Campaign → Product → Specs
- Approval status (ties to `job_output_comments` schema)

**Acceptance**

- [ ] Click any output → full lineage in ≤3 hops
- [ ] L10 ≥ 4 — relationship navigation without file paths in creator mode

### Phase 5 — Run diff + Pipelines (1 sprint)

**Deliverables**

- Compare two runs: spec field diff + manifest hash diff + optional SSIM on outputs
- Pipelines read-only: ProductSpec → … → Provider (GitHub commit viewer UX)
- Weave-style graph **only** inside `/runs/:id` and `/pipelines`

**Acceptance**

- [ ] Diff Run A vs B shows CreativeSpec lighting change + cost delta
- [ ] Pipelines route read-only; no edit affordances

### Phase 6 — Enterprise + polish (later)

- Workspace → Brands hierarchy
- Knowledge package manager UI
- Providers admin (dev only)

---

## Audit gates (per phase)

Register target in `control_tower/registry/ui-ux-audit-paths.yaml`:

```yaml
  image-pipeline-ui:
    bundle_segment: image-pipeline-ui
    cross_repo_dependency_name: image-pipeline
```

**After each phase** (creator routes must be mounted):

```bash
# Terminal 1
cd ~/Developer/GitHub/services/image-pipeline && ./ui/dev.sh

# Terminal 2 (control_tower)
BUNDLE_ID=creative-ide-p{N}
python3 scripts/run_ui_codebase_preflight.py \
  --repo ~/Developer/GitHub/services/image-pipeline/ui/app \
  --target image-pipeline-ui --bundle-id "$BUNDLE_ID"

python3 scripts/run_ui_ux_audit.py urls \
  --url http://127.0.0.1:5173/ \
  --url http://127.0.0.1:5173/generate \
  --url http://127.0.0.1:5173/products \
  --url http://127.0.0.1:5173/runs \
  --bundle-id "$BUNDLE_ID" \
  --crawl-rendering browser
```

**Phase pass criteria:** `audit_result.yaml` → `overall_score` ≥ phase target; all P0 violations in remediation_tasks closed; L9 ≥ 4 from Phase 2 onward.

**Remediation loop:** `remediation_tasks.yaml` → BRIEF nodes → implement → re-audit until gate passes.

---

## Remediation tasks (seed for Phase 0 BRIEF)

```yaml
recommended_tasks:
  - title: Implement FractalOS glass primitive library
    layer: L9
    priority: P0
    acceptance_criteria:
      - GlassCard uses backdrop-filter blur 24px and 1px border
      - ContextCanvas visible on all routes
      - ARAGON indigo tokens in CSS variables only (no hardcoded hex in components)

  - title: Replace single-page IA with Creative IDE router
    layer: L2
    priority: P0
    acceptance_criteria:
      - Nav rail lists Products Campaigns Assets Generate Runs
      - Dashboard route at /
      - Developer mode gates Pipelines and Providers

  - title: Remove creator prompt surfaces
    layer: L10
    priority: P0
    acceptance_criteria:
      - No prompt textarea in creator mode
      - NewShotWizard prompt step replaced by Campaign picker

  - title: Floating glass layout shell
    layer: L4
    priority: P0
    acceptance_criteria:
      - Main content inset 24px from viewport
      - Panels use glass-card not opaque bg-panel
      - Active nav item uses glass-card--active
```

---

## Success metrics

| Metric | Target |
|--------|--------|
| Audit overall score | ≥ 72 ship, ≥ 84 mature |
| Creator task: new product shot | ≤ 5 min, 0 JSON edits |
| L2 IA score | ≥ 4 |
| L9 design system score | ≥ 4 |
| Critical violations | 0 at ship |
| Compile determinism | unchanged — UI is skin over compiler |

---

## References

- `docs/CREATIVE_COMPILER_LANGUAGE_SPEC.md` — language objects
- `knowledge/README.md` — Products/Campaigns/Operations
- `control_tower/skills/user/ui-ux-architecture-audit/SKILL.md` — L1–L10 framework
- `~/.claude/skills/fractalos-design-system/` — glass + ARAGON tokens
- Figma Weave screenshots — polish reference for Runs graph only
