# Phase 2 — Products + Campaigns PIM

**Status:** Planning (post Phase 1)  
**Duration:** ~2 sprints  
**Repo:** `services/image-pipeline`  
**Parent:** `docs/CREATIVE_IDE_UX_PLAN.md`

---

## Goal

Creators author **ProductSpec** and **CreativeSpec** entirely in the UI — structured fields only, zero prose prompts, zero JSON editing. Golden path: **Hairbands + Starz Kid** campaign authored end-to-end in forms.

---

## Scope

### In scope

| Surface | Route | Backend |
|---------|-------|---------|
| Product list | `/products` | `GET /api/knowledge/products` |
| Product editor | `/products/:slug` | `GET/PUT knowledge/products/{slug}.json` + Supabase `products`/`cad_specs` |
| Product Explorer | `/products/:slug` (tree panel) | VS Code outline: geometry, materials, logos, constraints, editing |
| Campaign list | `/campaigns` | `GET /api/knowledge/campaigns` |
| Campaign editor | `/campaigns/:id` | `GET/PUT knowledge/campaigns/{id}.json` + new `campaigns` table |
| Validation | forms | `product-spec-v2` + `creative-spec-v2` JSON Schema |

### Out of scope (Phase 3+)

- Generate execution / cloud queue
- Run diff, lineage drill-down
- Providers admin UI

---

## Data model

### Disk (compiler source of truth today)

```
knowledge/products/{slug}.json   → ProductSpec v2
knowledge/campaigns/{id}.json    → CreativeSpec v2
```

### Cloud (Phase 2b — parallel write)

| Table | Maps to | Notes |
|-------|---------|-------|
| `products` | ProductSpec.id, name | tenant-scoped |
| `cad_specs` | geometry, materials JSON | FK → products |
| `campaigns` *(new migration)* | CreativeSpec v2 | FK → product_spec_id |

**Write path:** UI saves → validate schema → write disk → mirror Supabase (async OK for v1).

---

## API design (new endpoints)

```
GET    /api/knowledge/products
GET    /api/knowledge/products/{slug}
PUT    /api/knowledge/products/{slug}     # validated body

GET    /api/knowledge/campaigns
GET    /api/knowledge/campaigns/{id}
PUT    /api/knowledge/campaigns/{id}

POST   /api/knowledge/products            # create slug
POST   /api/knowledge/campaigns           # create id
```

**Validation:** reuse `tests/test_spec_validator_v2.py` logic via `core/spec_validator.py` (or extract shared module).

---

## UI components (new)

| Component | Purpose |
|-----------|---------|
| `ProductListPage` | replace stub — table/cards with slug, name, campaign count |
| `ProductEditorPage` | tabbed form: Identity, Geometry, Materials, Brand/Logos, Constraints, Editing |
| `ProductExplorer` | left tree mirroring spec sections (read-only nav + scroll-to-section) |
| `CampaignListPage` | cards with mood, product link |
| `CampaignEditorPage` | Campaign meta, Scene (lighting/camera/background/composition), Scene locks |
| `StructuredField` | enum selects, numeric inputs, array editors — **no textarea for prompts** |
| `SchemaForm` | optional: generic renderer from JSON Schema for speed |

### Form field map (Hairbands golden path)

**Product** (`hairbands.json`):

- `name`, `immutable` (read-only true)
- `product.geometry`: description, symmetry, envelope
- `product.materials[]`: id, type, color, finish, texture
- `product.dimensions`: width_mm, circumference_mm
- `brand.logos[]`: id, asset upload, placement region, scale
- `constraints`, `editing` regions

**Campaign** (`starz-kid.json`):

- `campaign`: label, audience, mood, style
- `scene`: lighting, camera, composition, background, description (short structured sentence OK)
- `scene_locks[]`: id, title, invariants key-value

---

## Sprint breakdown

### Sprint 2a — Read + list + detail shell (1 week)

1. Knowledge CRUD API with schema validation
2. Products list + Campaigns list (live data from `knowledge/`)
3. Product detail route stub with Explorer tree (read-only)
4. Campaign detail route stub (read-only)
5. Tests: API round-trip Hairbands JSON unchanged

**Gate:** L2 ≥ 4, L10 ≥ 3 on `/products/:slug`

### Sprint 2b — Write forms + cloud mirror (1 week)

1. Product editor forms (all ProductSpec sections)
2. Campaign editor forms (CreativeSpec sections)
3. Logo asset upload → `refs/` + path in spec
4. Supabase migration `campaigns` + mirror writes
5. Create product / create campaign wizards (slug/id picker only)

**Gate:**

- [ ] Hairbands golden path authored entirely in UI (no JSON hand-edit)
- [ ] V2 closed — zero prompt boxes on Products/Campaigns
- [ ] L10 ≥ 4 on `/products/:slug`
- [ ] L9 ≥ 4 — tokens + glass only, no new hardcoded colors

---

## BRIEF decomposition (for Control Tower queue)

| Node | Work unit | Est. |
|------|-----------|------|
| P2-1 | Knowledge CRUD API + validators | 4h |
| P2-2 | Products list + detail shell + Explorer | 6h |
| P2-3 | Product editor forms (all sections) | 8h |
| P2-4 | Campaigns list + editor forms | 6h |
| P2-5 | Asset upload for logos | 4h |
| P2-6 | Supabase `campaigns` migration + mirror | 4h |
| P2-7 | Golden path QA + audit gate | 2h |

**Suggested BRIEF name:** `BRIEF::creative-ide-phase2-pim::{date}`  
**Supervision:** `AUTONOMOUS` for API/tests; `HITL` for schema/migration review.

---

## Risks

| Risk | Mitigation |
|------|------------|
| JSON Schema ↔ form drift | Single schema source; validate on every PUT |
| Logo upload path security | Reuse `_resolve_path` + allowed roots |
| Supabase vs disk conflict | Disk wins for compiler; cloud is mirror until Phase 3 |
| Form complexity (scene_locks) | Start with Hairbands fields only; generic array editor later |

---

## Acceptance (Phase 2 complete)

From `CREATIVE_IDE_UX_PLAN.md`:

- [ ] Hairbands golden path authored entirely in UI
- [ ] L10 ≥ 4 on `/products/:slug` — max 3 type sizes per card, no JSON default view
- [ ] V2 closed — zero prompt boxes on Products/Campaigns
- [ ] Audit overall ≥ 72, L9 ≥ 4

---

## References

- `knowledge/products/hairbands.json`
- `knowledge/campaigns/starz-kid.json`
- `schemas/product-spec-v2.schema.json`
- `schemas/creative-spec-v2.schema.json`
- `docs/CREATIVE_COMPILER_LANGUAGE_SPEC.md`
