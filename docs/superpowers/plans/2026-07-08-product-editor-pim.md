# Product Editor (Phase 2 PIM) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full read/write Product Editor at `/products/:slug` for `knowledge/products/{slug}.json` (product-spec-v2), and wire `core/spec_extractor.py` into a "new product from photos" UI path, so the `sps` (Shin Pad Sleeve) product can be authored end-to-end in forms with zero JSON hand-editing.

**Architecture:** Backend already has full CRUD for knowledge products (`GET/PUT /api/knowledge/products/{slug}`, validated via `spec_validator_v2.validate_product_spec_v2`) — no backend change needed for editing itself. Add one new backend capability: a photo → draft-spec extraction endpoint that wraps a new LLM-vision function in `core/spec_extractor.py` (mirrors the existing `ui/brief_drafter.py` pattern: `claude_agent_sdk.query()`, no billed API key). Frontend gets one new reusable `ProductForm` component (structured fields only, per the "zero prompt boxes" rule in `docs/CREATIVE_IDE_UX_PLAN.md`), used by both the rewritten `ProductDetailPage` (edit + save) and a new "Extract from photos" step in `ProductNewPage` (draft → review → create).

**Tech Stack:** FastAPI (`ui/server.py`), Python 3.10+ dep-free `core/`, React + Vite + TypeScript (`ui/app/`), `claude_agent_sdk.query()` for the vision draft (drives the local `claude` CLI, no API key).

## Global Constraints

- No `prompt_template` / prose textarea anywhere in Products UI — structured fields only (`docs/CREATIVE_IDE_UX_PLAN.md` V2, `docs/PHASE_2_PIM_PLAN.md` acceptance).
- All product spec writes must pass `core/spec_validator_v2.validate_product_spec_v2` before persisting (already enforced in `KnowledgeStore.save_product` — do not bypass it).
- `id` must match `^[a-z0-9][a-z0-9-]*$` everywhere (schema `schemas/product-spec-v2.schema.json`).
- Every knowledge-touching API route takes an optional `tenant: Optional[str] = None` query param and resolves via `_knowledge(tenant)` in `ui/server.py:351` — new routes must follow this exact pattern.
- No `ANTHROPIC_API_KEY` anywhere in this repo — LLM calls only via `claude_agent_sdk.query()` (subscription auth), same as `ui/brief_drafter.py`.
- Frontend has no test runner configured (`ui/app/package.json` only has `dev`/`build`/`preview`); the type-check gate is `npm run build` (`tsc -b && vite build`) — treat a clean `tsc -b` as the frontend correctness gate for each task.
- Backend tests run via `python3 -m pytest tests/ -q` (plain `unittest.TestCase` classes, no fixtures framework).

---

### Task 1: `ProductSpecV2` TypeScript type + extraction response type

**Files:**
- Modify: `ui/app/src/types.ts`

**Interfaces:**
- Produces: `ProductSpecV2`, `Material`, `Logo`, `ProductReference`, `ProductExtractResponse` types, imported by `ProductForm.tsx`, `ProductDetailPage.tsx`, `ProductNewPage.tsx` in later tasks.

- [ ] **Step 1: Add the types**

Append to `ui/app/src/types.ts`:

```ts
export type Material = {
  id: string;
  type?: string;
  color?: string;
  finish?: string;
  texture?: string;
};

export type Logo = {
  id: string;
  asset?: string;
  placement: { region: string; anchor?: string; offset_x?: number; offset_y?: number };
  scale?: number;
  rotation?: number;
  safe_zone?: string;
};

export type ProductReference = { id: string; role: string; asset: string };

export type ProductSpecV2 = {
  schema_version: "product-spec-v2";
  id: string;
  name: string;
  immutable?: boolean;
  product: {
    geometry: { description: string; symmetry?: string; envelope?: string };
    materials?: Material[];
    dimensions?: Record<string, string | number>;
  };
  brand?: { logos?: Logo[] };
  constraints: {
    preserve_geometry: boolean;
    preserve_material: boolean;
    preserve_proportions: boolean;
  };
  editing: {
    protected_regions: string[];
    editable_regions: string[];
  };
  references?: ProductReference[];
  source?: { knowledge_path?: string; content_hash?: string };
};

export type ProductExtractResponse = {
  draft: Partial<ProductSpecV2> & { suggested_name?: string };
};
```

- [ ] **Step 2: Type-check**

Run: `cd ui/app && npx tsc -b --noEmit`
Expected: no errors (new exported types are unused so far — that's fine, `tsc` doesn't flag unused exports).

- [ ] **Step 3: Commit**

```bash
git add ui/app/src/types.ts
git commit -m "feat(ui): add ProductSpecV2 types for the Product Editor"
```

---

### Task 2: `emptyProductSpec` helper + `ProductForm` component

**Files:**
- Create: `ui/app/src/lib/emptyProductSpec.ts`
- Create: `ui/app/src/components/ProductForm.tsx`

**Interfaces:**
- Consumes: `ProductSpecV2`, `Material`, `Logo` from Task 1 (`ui/app/src/types.ts`).
- Produces: `emptyProductSpec(id: string, name: string): ProductSpecV2` and `<ProductForm value={spec} onChange={(next: ProductSpecV2) => void} disabledId?: boolean />`, consumed by `ProductDetailPage` (Task 3) and `ProductNewPage` (Task 5).

- [ ] **Step 1: `emptyProductSpec` helper**

```ts
// ui/app/src/lib/emptyProductSpec.ts
import type { ProductSpecV2 } from "../types";

export function emptyProductSpec(id: string, name: string): ProductSpecV2 {
  return {
    schema_version: "product-spec-v2",
    id,
    name,
    immutable: true,
    product: {
      geometry: { description: "", symmetry: "", envelope: "" },
      materials: [{ id: "primary", type: "", color: "", finish: "", texture: "" }],
      dimensions: {},
    },
    brand: { logos: [] },
    constraints: { preserve_geometry: true, preserve_material: true, preserve_proportions: true },
    editing: { protected_regions: [], editable_regions: [] },
    references: [],
  };
}
```

- [ ] **Step 2: `ProductForm` component**

```tsx
// ui/app/src/components/ProductForm.tsx
import type { ProductSpecV2, Material, Logo } from "../types";

type Props = {
  value: ProductSpecV2;
  onChange: (next: ProductSpecV2) => void;
  disabledId?: boolean;
};

function StringListEditor({
  label,
  values,
  onChange,
  placeholder,
}: {
  label: string;
  values: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="field-label">{label}</label>
      {values.map((v, i) => (
        <div key={i} style={{ display: "flex", gap: "0.4rem", marginBottom: "0.35rem" }}>
          <input
            className="field-input"
            value={v}
            placeholder={placeholder}
            onChange={(e) => {
              const next = [...values];
              next[i] = e.target.value;
              onChange(next);
            }}
          />
          <button type="button" className="btn btn--sm" onClick={() => onChange(values.filter((_, j) => j !== i))}>
            Remove
          </button>
        </div>
      ))}
      <button type="button" className="btn btn--sm" onClick={() => onChange([...values, ""])}>
        + Add
      </button>
    </div>
  );
}

export default function ProductForm({ value, onChange, disabledId = false }: Props) {
  const geometry = value.product.geometry;
  const materials = value.product.materials || [];
  const dimensions = value.product.dimensions || {};
  const logos = value.brand?.logos || [];
  const constraints = value.constraints;
  const editing = value.editing;

  const set = (patch: Partial<ProductSpecV2>) => onChange({ ...value, ...patch });

  const setMaterial = (i: number, patch: Partial<Material>) => {
    const next = materials.map((m, j) => (j === i ? { ...m, ...patch } : m));
    set({ product: { ...value.product, materials: next } });
  };
  const addMaterial = () =>
    set({
      product: {
        ...value.product,
        materials: [...materials, { id: `material-${materials.length + 1}`, type: "", color: "", finish: "", texture: "" }],
      },
    });
  const removeMaterial = (i: number) =>
    set({ product: { ...value.product, materials: materials.filter((_, j) => j !== i) } });

  const dimensionEntries = Object.entries(dimensions);
  const setDimension = (key: string, newKey: string, val: string) => {
    const next: Record<string, string | number> = {};
    for (const [k, v] of dimensionEntries) next[k === key ? newKey : k] = k === key ? val : v;
    if (!(newKey in next)) next[newKey] = val;
    set({ product: { ...value.product, dimensions: next } });
  };
  const addDimension = () => set({ product: { ...value.product, dimensions: { ...dimensions, "": "" } } });
  const removeDimension = (key: string) => {
    const next = { ...dimensions };
    delete next[key];
    set({ product: { ...value.product, dimensions: next } });
  };

  const setLogo = (i: number, patch: Partial<Logo>) => {
    const next = logos.map((l, j) => (j === i ? { ...l, ...patch } : l));
    set({ brand: { ...value.brand, logos: next } });
  };
  const addLogo = () =>
    set({
      brand: {
        ...value.brand,
        logos: [...logos, { id: `logo-${logos.length + 1}`, placement: { region: "" }, scale: 1 }],
      },
    });
  const removeLogo = (i: number) => set({ brand: { ...value.brand, logos: logos.filter((_, j) => j !== i) } });

  return (
    <div className="structured-form">
      <div>
        <label className="field-label" htmlFor="pf-name">
          Product name
        </label>
        <input
          id="pf-name"
          className="field-input"
          value={value.name}
          onChange={(e) => set({ name: e.target.value })}
          required
        />
      </div>
      <div>
        <label className="field-label" htmlFor="pf-id">
          ID (slug)
        </label>
        <input id="pf-id" className="field-input" value={value.id} disabled={disabledId} readOnly={disabledId} />
      </div>

      <h2 className="dashboard-panel__title">Geometry</h2>
      <div>
        <label className="field-label" htmlFor="pf-geo-desc">
          Description
        </label>
        <textarea
          id="pf-geo-desc"
          className="field-input field-textarea"
          rows={3}
          value={geometry.description}
          onChange={(e) =>
            set({ product: { ...value.product, geometry: { ...geometry, description: e.target.value } } })
          }
          required
        />
      </div>
      <div style={{ display: "flex", gap: "0.75rem" }}>
        <div style={{ flex: 1 }}>
          <label className="field-label" htmlFor="pf-geo-sym">
            Symmetry
          </label>
          <input
            id="pf-geo-sym"
            className="field-input"
            value={geometry.symmetry || ""}
            onChange={(e) => set({ product: { ...value.product, geometry: { ...geometry, symmetry: e.target.value } } })}
          />
        </div>
        <div style={{ flex: 1 }}>
          <label className="field-label" htmlFor="pf-geo-env">
            Envelope
          </label>
          <input
            id="pf-geo-env"
            className="field-input"
            value={geometry.envelope || ""}
            onChange={(e) => set({ product: { ...value.product, geometry: { ...geometry, envelope: e.target.value } } })}
          />
        </div>
      </div>

      <h2 className="dashboard-panel__title">Materials</h2>
      {materials.map((m, i) => (
        <div key={i} className="glass-card" style={{ padding: "0.75rem", marginBottom: "0.5rem" }}>
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            {(["id", "type", "color", "finish", "texture"] as const).map((field) => (
              <div key={field} style={{ flex: "1 1 120px" }}>
                <label className="field-label">{field}</label>
                <input
                  className="field-input"
                  value={(m[field] as string) || ""}
                  onChange={(e) => setMaterial(i, { [field]: e.target.value } as Partial<Material>)}
                />
              </div>
            ))}
          </div>
          <button type="button" className="btn btn--sm" style={{ marginTop: "0.5rem" }} onClick={() => removeMaterial(i)}>
            Remove material
          </button>
        </div>
      ))}
      <button type="button" className="btn btn--sm" onClick={addMaterial}>
        + Add material
      </button>

      <h2 className="dashboard-panel__title">Dimensions</h2>
      {dimensionEntries.map(([k, v]) => (
        <div key={k} style={{ display: "flex", gap: "0.4rem", marginBottom: "0.35rem" }}>
          <input className="field-input" placeholder="key" value={k} onChange={(e) => setDimension(k, e.target.value, String(v))} />
          <input className="field-input" placeholder="value" value={String(v)} onChange={(e) => setDimension(k, k, e.target.value)} />
          <button type="button" className="btn btn--sm" onClick={() => removeDimension(k)}>
            Remove
          </button>
        </div>
      ))}
      <button type="button" className="btn btn--sm" onClick={addDimension}>
        + Add dimension
      </button>

      <h2 className="dashboard-panel__title">Brand / logos</h2>
      {logos.map((l, i) => (
        <div key={i} className="glass-card" style={{ padding: "0.75rem", marginBottom: "0.5rem" }}>
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            <div style={{ flex: "1 1 100px" }}>
              <label className="field-label">id</label>
              <input className="field-input" value={l.id} onChange={(e) => setLogo(i, { id: e.target.value })} />
            </div>
            <div style={{ flex: "1 1 100px" }}>
              <label className="field-label">placement region</label>
              <input
                className="field-input"
                value={l.placement?.region || ""}
                onChange={(e) => setLogo(i, { placement: { ...l.placement, region: e.target.value } })}
              />
            </div>
            <div style={{ flex: "1 1 80px" }}>
              <label className="field-label">scale</label>
              <input
                className="field-input"
                type="number"
                step="0.1"
                value={l.scale ?? 1}
                onChange={(e) => setLogo(i, { scale: Number(e.target.value) })}
              />
            </div>
          </div>
          <button type="button" className="btn btn--sm" style={{ marginTop: "0.5rem" }} onClick={() => removeLogo(i)}>
            Remove logo
          </button>
        </div>
      ))}
      <button type="button" className="btn btn--sm" onClick={addLogo}>
        + Add logo
      </button>

      <h2 className="dashboard-panel__title">Constraints</h2>
      <div style={{ display: "flex", gap: "1rem" }}>
        {(["preserve_geometry", "preserve_material", "preserve_proportions"] as const).map((key) => (
          <label key={key} style={{ display: "flex", alignItems: "center", gap: "0.35rem", fontSize: "0.85rem" }}>
            <input
              type="checkbox"
              checked={constraints[key]}
              onChange={(e) => set({ constraints: { ...constraints, [key]: e.target.checked } })}
            />
            {key}
          </label>
        ))}
      </div>

      <h2 className="dashboard-panel__title">Editing regions</h2>
      <StringListEditor
        label="Protected regions"
        values={editing.protected_regions}
        onChange={(next) => set({ editing: { ...editing, protected_regions: next } })}
        placeholder="e.g. logo, band"
      />
      <StringListEditor
        label="Editable regions"
        values={editing.editable_regions}
        onChange={(next) => set({ editing: { ...editing, editable_regions: next } })}
        placeholder="e.g. background, scene"
      />
    </div>
  );
}
```

- [ ] **Step 3: Type-check**

Run: `cd ui/app && npx tsc -b --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add ui/app/src/lib/emptyProductSpec.ts ui/app/src/components/ProductForm.tsx
git commit -m "feat(ui): add ProductForm structured editor + emptyProductSpec helper"
```

---

### Task 3: Rewrite `ProductDetailPage` to edit + save via `ProductForm`

**Files:**
- Modify: `ui/app/src/pages/ProductDetailPage.tsx`

**Interfaces:**
- Consumes: `ProductForm`, `emptyProductSpec` (Task 2), `ProductSpecV2` (Task 1), existing `api`, `useTenant`/`withTenant` (`ui/api.ts`, `ui/TenantContext.tsx`).
- Produces: no new exports — this is a leaf page.

- [ ] **Step 1: Replace the read-only knowledge branch with an editable form + Save**

Replace the whole file's knowledge-loading state and the `isKnowledge` render branch. Full new file:

```tsx
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import ArtifactGallery from "../components/ArtifactGallery";
import BriefModal from "../components/BriefModal";
import ProductForm from "../components/ProductForm";
import GlassCard from "../design/GlassCard";
import { api } from "../api";
import { useTenant, withTenant } from "../TenantContext";
import type { ProductSpecV2 } from "../types";

type Shot = { id: string; label: string; path: string; dir: string };

type JobsOnlyProduct = { id: string; name: string; shots?: Shot[] };

type ProductSource = "knowledge" | "jobs_only";

export default function ProductDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const [product, setProduct] = useState<ProductSpecV2 | JobsOnlyProduct | null>(null);
  const [source, setSource] = useState<ProductSource | null>(null);
  const [draft, setDraft] = useState<ProductSpecV2 | null>(null);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [status, setStatus] = useState("");
  const [showBrief, setShowBrief] = useState(false);
  const [galleryRefreshKey, setGalleryRefreshKey] = useState(0);
  const { tenant } = useTenant();

  const load = () => {
    if (!slug) return;
    const params = withTenant(new URLSearchParams(), tenant);
    api<{ product: ProductSpecV2 | JobsOnlyProduct; source: ProductSource }>(`/api/products/${slug}?${params}`)
      .then((d) => {
        setProduct(d.product);
        setSource(d.source);
        if (d.source === "knowledge") setDraft(d.product as ProductSpecV2);
      })
      .catch((e) => setStatus(String(e)));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, tenant]);

  const onUpload = async (file: File) => {
    if (!slug) return;
    setUploading(true);
    setStatus("");
    const form = new FormData();
    form.append("file", file);
    try {
      const params = withTenant(
        new URLSearchParams({ ref_id: `ref-${Date.now()}`, role: "product_ref" }),
        tenant
      );
      const res = await fetch(`/api/knowledge/products/${slug}/refs?${params}`, {
        method: "POST",
        body: form,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      setStatus(`Uploaded ${data.path}`);
      load();
    } catch (e) {
      setStatus(String(e));
    } finally {
      setUploading(false);
    }
  };

  const onSave = async () => {
    if (!draft || !slug) return;
    setSaving(true);
    setSaveError("");
    try {
      const params = withTenant(new URLSearchParams(), tenant);
      await api(`/api/knowledge/products/${slug}?${params}`, {
        method: "PUT",
        body: JSON.stringify(draft),
      });
      setStatus("Saved.");
      load();
    } catch (e) {
      setSaveError(String(e));
    } finally {
      setSaving(false);
    }
  };

  if (!product) {
    return <GlassCard className="stub-note">{status || "Loading…"}</GlassCard>;
  }

  const isKnowledge = source === "knowledge";
  const refs = isKnowledge ? (product as ProductSpecV2).references || [] : [];

  return (
    <div>
      <Link to="/products" className="btn btn--sm" style={{ marginBottom: "0.75rem", display: "inline-block" }}>
        ← Products
      </Link>
      <h1 className="page-title">{product.name}</h1>
      <p className="page-subtitle">
        {isKnowledge
          ? `ProductSpec · ${product.id}`
          : `${(product as JobsOnlyProduct).shots?.length ?? 0} job(s) on disk · ${(product as JobsOnlyProduct).id}`}
      </p>

      {isKnowledge && draft ? (
        <div className="page-grid product-detail-grid" style={{ gap: "1rem" }}>
          <GlassCard className="form-panel">
            <ProductForm value={draft} onChange={setDraft} disabledId />
            {saveError && <p className="form-error">{saveError}</p>}
            <div className="form-actions">
              <button type="button" className="btn btn--primary-solid" onClick={onSave} disabled={saving}>
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          </GlassCard>

          <GlassCard className="form-panel">
            <h2 className="dashboard-panel__title">References</h2>
            {refs.length === 0 ? (
              <p className="dashboard-empty">No references yet.</p>
            ) : (
              <ul className="explorer-list">
                {refs.map((r) => (
                  <li key={r.id}>
                    {r.id} ({r.role}) — {r.asset}
                  </li>
                ))}
              </ul>
            )}
            <div style={{ marginTop: "1rem" }}>
              <label htmlFor="product-ref-upload" className="field-label">
                Upload reference
              </label>
              <input
                id="product-ref-upload"
                type="file"
                accept="image/*"
                disabled={uploading}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) onUpload(f);
                }}
              />
            </div>
            {status && (
              <p role="status" style={{ marginTop: "0.5rem", fontSize: "0.8rem", color: "var(--color-text-tertiary)" }}>
                {status}
              </p>
            )}
          </GlassCard>
        </div>
      ) : (
        <GlassCard className="form-panel">
          <p className="page-subtitle" style={{ margin: 0 }}>
            No knowledge ProductSpec for this product yet — it exists as job files on disk only.
            Geometry, materials, and reference uploads need a ProductSpec first.
          </p>
          {(product as JobsOnlyProduct).shots && (product as JobsOnlyProduct).shots!.length > 0 && (
            <ul className="explorer-list" style={{ marginTop: "0.75rem" }}>
              {(product as JobsOnlyProduct).shots!.map((s) => (
                <li key={s.path}>{s.label}</li>
              ))}
            </ul>
          )}
        </GlassCard>
      )}

      <div style={{ marginTop: "1rem", display: "flex", gap: "0.5rem" }}>
        <Link to="/campaigns/new" className="btn">
          + Campaign for this product
        </Link>
        <button type="button" className="btn btn--primary-solid" onClick={() => setShowBrief(true)}>
          + New shot
        </button>
      </div>

      {showBrief && (
        <BriefModal
          productSlug={product.id}
          onClose={() => setShowBrief(false)}
          onGenerated={() => setGalleryRefreshKey((k) => k + 1)}
        />
      )}

      <ArtifactGallery key={galleryRefreshKey} productSlug={product.id} title="Recent shots" />
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd ui/app && npx tsc -b --noEmit`
Expected: no errors.

- [ ] **Step 3: Manual verification**

Run: `./ui/run.sh` (starts on `:8787`), open `http://localhost:8787/products/hairbands`.
Expected: geometry/materials/dimensions/brand/constraints/editing fields render pre-filled from `knowledge/products/hairbands.json`; edit the geometry description, click Save; reload the page and confirm the edit persisted (check `knowledge/products/hairbands.json` on disk, then revert the manual edit so the fixture stays clean for other tests).

- [ ] **Step 4: Commit**

```bash
git add ui/app/src/pages/ProductDetailPage.tsx
git commit -m "feat(ui): Product Editor — edit-in-place form with Save on /products/:slug"
```

---

### Task 4: `core/spec_extractor.py` — vision draft from photos

**Files:**
- Modify: `core/spec_extractor.py`
- Test: `tests/test_spec_extractor_photos.py`

**Interfaces:**
- Consumes: none new (stdlib `json`, `re`; lazy-imports `claude_agent_sdk` exactly like `ui/brief_drafter.py:42`).
- Produces: `SpecExtractError(RuntimeError)`, `async def extract_product_spec_from_photos(photo_paths: list[str], *, name_hint: str | None = None, notes: str | None = None) -> dict[str, Any]` — consumed by the new endpoint in Task 5.

- [ ] **Step 1: Write the failing test (mocks the SDK call, doesn't hit the network/CLI)**

```python
# tests/test_spec_extractor_photos.py
"""Tests for core/spec_extractor.extract_product_spec_from_photos — mocked SDK, no live LLM call."""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import spec_extractor  # noqa: E402


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


async def _fake_query_ok(*, prompt, options):  # noqa: ANN001
    yield _FakeMessage(
        '{"schema_version": "product-spec-v2", "id": "shin-pad-sleeve", "name": "Shin Pad Sleeve", '
        '"product": {"geometry": {"description": "Black compression sleeve worn over the shin"}}, '
        '"constraints": {"preserve_geometry": true, "preserve_material": true, "preserve_proportions": true}, '
        '"editing": {"protected_regions": ["sleeve"], "editable_regions": ["background"]}}'
    )


async def _fake_query_fenced(*, prompt, options):  # noqa: ANN001
    yield _FakeMessage('```json\n{"schema_version": "product-spec-v2", "id": "x", "name": "X"}\n```')


class TestExtractProductSpecFromPhotos(unittest.IsolatedAsyncioTestCase):
    async def test_parses_json_draft_from_model_output(self):
        fake_sdk = mock.MagicMock()
        fake_sdk.query = _fake_query_ok
        fake_sdk.ClaudeAgentOptions = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk}):
            draft = await spec_extractor.extract_product_spec_from_photos(
                ["/tmp/flatlay.jpg"], name_hint="Shin Pad Sleeve"
            )
        self.assertEqual(draft["schema_version"], "product-spec-v2")
        self.assertEqual(draft["id"], "shin-pad-sleeve")
        self.assertIn("sleeve", draft["product"]["geometry"]["description"].lower())

    async def test_strips_markdown_fences(self):
        fake_sdk = mock.MagicMock()
        fake_sdk.query = _fake_query_fenced
        fake_sdk.ClaudeAgentOptions = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk}):
            draft = await spec_extractor.extract_product_spec_from_photos(["/tmp/a.jpg"])
        self.assertEqual(draft["id"], "x")

    async def test_raises_on_non_json_output(self):
        async def fake_query_bad(*, prompt, options):  # noqa: ANN001
            yield _FakeMessage("not json at all")

        fake_sdk = mock.MagicMock()
        fake_sdk.query = fake_query_bad
        fake_sdk.ClaudeAgentOptions = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk}):
            with self.assertRaises(spec_extractor.SpecExtractError):
                await spec_extractor.extract_product_spec_from_photos(["/tmp/a.jpg"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_spec_extractor_photos.py -v`
Expected: FAIL — `AttributeError: module 'spec_extractor' has no attribute 'extract_product_spec_from_photos'` (or `SpecExtractError`).

- [ ] **Step 3: Implement**

Add `import json` to the existing imports at the top of `core/spec_extractor.py` (it currently imports `hashlib, os, re`; add `json` alongside them). Then append to the end of `core/spec_extractor.py`:

```python
_EXTRACT_RULES = """You draft a product-spec-v2 JSON object (a deterministic ProductSpec, no prose prompts) \
from reference photos of a physical product, for a creative-image compiler's Product Information \
Management system. Use the Read tool to open each photo path given to you, then look at the images \
and output ONLY a single JSON object shaped like this (fill every field; use a best-guess placeholder \
only when truly unobservable — never omit a required field):

{
  "schema_version": "product-spec-v2",
  "id": "<slug-from-name>",
  "name": "<Product Name>",
  "immutable": true,
  "product": {
    "geometry": {"description": "<what it is: shape, form factor>", "symmetry": "<bilateral|radial|none|...>", "envelope": "<short shape-primitive tag, e.g. torus_loop, rectangular_prism>"},
    "materials": [{"id": "primary", "type": "<material type>", "color": "<dominant color>", "finish": "<matte|glossy|woven|...>", "texture": "<surface texture>"}],
    "dimensions": {}
  },
  "brand": {"logos": []},
  "constraints": {"preserve_geometry": true, "preserve_material": true, "preserve_proportions": true},
  "editing": {"protected_regions": ["<parts of the product that must never change>"], "editable_regions": ["background", "scene"]}
}

Rules:
- schema_version, id, name, product.geometry.description, constraints.*, editing.* are all required — never omit them.
- id must be a lowercase slug: [a-z0-9][a-z0-9-]*.
- Never invent brand names or logos you cannot actually see in the photos — leave brand.logos empty if unsure.
- Output ONLY the JSON object. No markdown fences, no preamble, no explanation."""


class SpecExtractError(RuntimeError):
    pass


async def extract_product_spec_from_photos(
    photo_paths: list[str],
    *,
    name_hint: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Vision-draft a product-spec-v2 dict from reference photos (LLM, not saved/validated)."""
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
    except ImportError as exc:
        raise SpecExtractError(
            "claude-agent-sdk not installed — pip install -r ui/requirements.txt"
        ) from exc

    photos_block = "\n".join(f"- {p}" for p in photo_paths)
    parts = [f"Reference photo file paths (use the Read tool to open each one):\n{photos_block}"]
    if name_hint:
        parts.append(f"Product name hint: {name_hint}")
    if notes:
        parts.append(f"Notes from the operator: {notes}")
    user_msg = "\n\n".join(parts)

    options = ClaudeAgentOptions(
        system_prompt=_EXTRACT_RULES,
        tools=["Read"],
        max_turns=4,
        permission_mode="bypassPermissions",
    )

    text_parts: list[str] = []
    try:
        async for message in query(prompt=user_msg, options=options):
            for block in getattr(message, "content", []) or []:
                text = getattr(block, "text", None)
                if text:
                    text_parts.append(text)
    except Exception as exc:  # claude_agent_sdk raises its own exception hierarchy
        raise SpecExtractError(f"Claude Agent SDK call failed: {exc}") from exc

    raw = "".join(text_parts).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        draft = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SpecExtractError(f"Model did not return valid JSON: {exc}\n{raw[:500]}") from exc
    if not isinstance(draft, dict):
        raise SpecExtractError("Model output must be a JSON object.")
    return draft
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_spec_extractor_photos.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add core/spec_extractor.py tests/test_spec_extractor_photos.py
git commit -m "feat(core): vision-draft product-spec-v2 from photos in spec_extractor"
```

---

### Task 5: `POST /api/knowledge/products/extract` endpoint

**Files:**
- Modify: `ui/server.py`
- Test: `tests/test_server_extract_endpoint.py`

**Interfaces:**
- Consumes: `spec_extractor.extract_product_spec_from_photos`, `spec_extractor.SpecExtractError` (Task 4).
- Produces: `POST /api/knowledge/products/extract` (multipart form: `photos: UploadFile[]`, `name: str = ""`, `notes: str = ""`) → `{"draft": {...}}`, consumed by `ProductNewPage` (Task 6).

- [ ] **Step 1: Add imports**

In `ui/server.py`, add `tempfile` to the stdlib imports (currently `asyncio, json, os, re, subprocess, sys, uuid`) and add `Form` to the fastapi import on line 26 (`from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile`). Add `import spec_extractor  # noqa: E402` to the block of `core`-relative imports around line 64-71 (alongside `spec_validator_v2`).

- [ ] **Step 2: Write the failing test**

```python
# tests/test_server_extract_endpoint.py
"""Tests for POST /api/knowledge/products/extract — mocked spec_extractor, no live LLM call."""
from __future__ import annotations

import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
import spec_extractor  # noqa: E402


class TestExtractEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(server.app)

    def test_extract_returns_draft_from_photos(self):
        fake_draft = {
            "schema_version": "product-spec-v2",
            "id": "shin-pad-sleeve",
            "name": "Shin Pad Sleeve",
        }

        async def fake_extract(photo_paths, *, name_hint=None, notes=None):
            self.assertTrue(len(photo_paths) == 1)
            self.assertTrue(os.path.isfile(photo_paths[0]))
            return fake_draft

        with mock.patch.object(spec_extractor, "extract_product_spec_from_photos", fake_extract):
            with mock.patch.object(server, "spec_extractor", spec_extractor):
                res = self.client.post(
                    "/api/knowledge/products/extract",
                    data={"name": "Shin Pad Sleeve", "notes": ""},
                    files={"photos": ("flatlay.jpg", io.BytesIO(b"fake-bytes"), "image/jpeg")},
                )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["draft"], fake_draft)

    def test_extract_requires_at_least_one_photo(self):
        res = self.client.post("/api/knowledge/products/extract", data={"name": "X"}, files={})
        self.assertEqual(res.status_code, 422)  # FastAPI rejects missing required File(...) field

    def test_extract_maps_spec_extract_error_to_422(self):
        async def fake_extract_raises(photo_paths, *, name_hint=None, notes=None):
            raise spec_extractor.SpecExtractError("model did not return JSON")

        with mock.patch.object(spec_extractor, "extract_product_spec_from_photos", fake_extract_raises):
            with mock.patch.object(server, "spec_extractor", spec_extractor):
                res = self.client.post(
                    "/api/knowledge/products/extract",
                    data={},
                    files={"photos": ("a.jpg", io.BytesIO(b"x"), "image/jpeg")},
                )
        self.assertEqual(res.status_code, 422)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_server_extract_endpoint.py -v`
Expected: FAIL with 404 (route doesn't exist yet).

- [ ] **Step 4: Implement the endpoint**

Add directly after the `knowledge_upload_product_ref` handler in `ui/server.py` (after the block ending around line 572):

```python
@app.post("/api/knowledge/products/extract")
async def knowledge_extract_product(
    name: str = Form(""),
    notes: str = Form(""),
    photos: list[UploadFile] = File(...),
) -> dict[str, Any]:
    if not photos:
        raise HTTPException(status_code=400, detail="at least one photo is required")
    with tempfile.TemporaryDirectory(prefix="pim-extract-") as tmp:
        photo_paths: list[str] = []
        for i, photo in enumerate(photos):
            if not photo.filename:
                continue
            safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", photo.filename).strip("-") or f"photo-{i}.png"
            dest = Path(tmp) / f"{i:02d}-{safe}"
            dest.write_bytes(await photo.read())
            photo_paths.append(str(dest))
        if not photo_paths:
            raise HTTPException(status_code=400, detail="no valid photo files uploaded")
        try:
            draft = await spec_extractor.extract_product_spec_from_photos(
                photo_paths, name_hint=name or None, notes=notes or None
            )
        except spec_extractor.SpecExtractError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"draft": draft}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_server_extract_endpoint.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the full backend suite to check no regressions**

Run: `python3 -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add ui/server.py tests/test_server_extract_endpoint.py
git commit -m "feat(ui): POST /api/knowledge/products/extract — photo-to-draft-spec endpoint"
```

---

### Task 6: "Extract from photos" flow in `ProductNewPage`

**Files:**
- Modify: `ui/app/src/pages/ProductNewPage.tsx`
- Modify: `ui/app/src/api.ts`

**Interfaces:**
- Consumes: `ProductForm`, `emptyProductSpec` (Task 2), `ProductSpecV2`, `ProductExtractResponse` (Task 1), `POST /api/knowledge/products/extract` (Task 5).
- Produces: no new exports — leaf page; extends `api.ts` with `extractProductFromPhotos`.

- [ ] **Step 1: Add an `extractProductFromPhotos` helper to `api.ts`**

Append to `ui/app/src/api.ts`:

```ts
export async function extractProductFromPhotos(
  photos: File[],
  name: string,
  notes: string
): Promise<{ draft: Record<string, unknown> }> {
  const form = new FormData();
  form.append("name", name);
  form.append("notes", notes);
  photos.forEach((f) => form.append("photos", f));
  const res = await fetch("/api/knowledge/products/extract", { method: "POST", body: form });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((data as { detail?: string }).detail || res.statusText);
  }
  return data as { draft: Record<string, unknown> };
}
```

- [ ] **Step 2: Rewrite `ProductNewPage.tsx`  with a mode switch (blank vs. from-photos)**

```tsx
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import GlassCard from "../design/GlassCard";
import ProductForm from "../components/ProductForm";
import { api, extractProductFromPhotos, uploadFile } from "../api";
import { emptyProductSpec } from "../lib/emptyProductSpec";
import { useTenant, withTenant } from "../TenantContext";
import type { ProductSpecV2 } from "../types";

const slugify = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");

function BlankProductForm() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [geometry, setGeometry] = useState("");
  const [materialColor, setMaterialColor] = useState("neutral");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const onName = (v: string) => {
    setName(v);
    if (!slug || slug === slugify(name)) setSlug(slugify(v));
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      await api("/api/knowledge/products", {
        method: "POST",
        body: JSON.stringify({
          id: slug || slugify(name),
          name,
          geometry_description: geometry,
          material_color: materialColor,
        }),
      });
      navigate(`/products/${slug || slugify(name)}`);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <GlassCard className="form-panel">
      <form onSubmit={onSubmit} className="structured-form">
        <div>
          <label htmlFor="product-name" className="field-label">
            Product name
          </label>
          <input id="product-name" className="field-input" value={name} onChange={(e) => onName(e.target.value)} required />
        </div>
        <div>
          <label htmlFor="product-slug" className="field-label">
            ID (slug)
          </label>
          <input
            id="product-slug"
            className="field-input"
            value={slug}
            onChange={(e) => setSlug(slugify(e.target.value))}
            pattern="[a-z0-9][a-z0-9-]*"
            required
          />
        </div>
        <div>
          <label htmlFor="product-geometry" className="field-label">
            Geometry description
          </label>
          <textarea
            id="product-geometry"
            className="field-input field-textarea"
            rows={3}
            value={geometry}
            onChange={(e) => setGeometry(e.target.value)}
            placeholder="What is the object? Shape, symmetry, envelope…"
            required
          />
        </div>
        <div>
          <label htmlFor="product-color" className="field-label">
            Primary material color
          </label>
          <input id="product-color" className="field-input" value={materialColor} onChange={(e) => setMaterialColor(e.target.value)} />
        </div>
        {error && <p className="form-error">{error}</p>}
        <div className="form-actions">
          <Link to="/products" className="btn">
            Cancel
          </Link>
          <button type="submit" className="btn btn--primary-solid" disabled={loading}>
            Create product
          </button>
        </div>
      </form>
    </GlassCard>
  );
}

function FromPhotosFlow() {
  const navigate = useNavigate();
  const { tenant } = useTenant();
  const [photos, setPhotos] = useState<File[]>([]);
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [extracting, setExtracting] = useState(false);
  const [extractError, setExtractError] = useState("");
  const [draft, setDraft] = useState<ProductSpecV2 | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  const onExtract = async () => {
    if (photos.length === 0) return;
    setExtracting(true);
    setExtractError("");
    try {
      const { draft: raw } = await extractProductFromPhotos(photos, name, notes);
      const id = (raw.id as string) || slugify((raw.name as string) || name) || "new-product";
      const base = emptyProductSpec(id, (raw.name as string) || name || id);
      const merged: ProductSpecV2 = {
        ...base,
        ...raw,
        id,
        product: { ...base.product, ...(raw.product as Partial<ProductSpecV2["product"]>) },
        constraints: { ...base.constraints, ...(raw.constraints as Partial<ProductSpecV2["constraints"]>) },
        editing: { ...base.editing, ...(raw.editing as Partial<ProductSpecV2["editing"]>) },
        brand: { ...base.brand, ...(raw.brand as Partial<ProductSpecV2["brand"]>) },
      } as ProductSpecV2;
      setDraft(merged);
    } catch (e) {
      setExtractError(String(e));
    } finally {
      setExtracting(false);
    }
  };

  const onSave = async () => {
    if (!draft) return;
    setSaving(true);
    setSaveError("");
    try {
      await api("/api/knowledge/products", {
        method: "POST",
        body: JSON.stringify({
          id: draft.id,
          name: draft.name,
          geometry_description: draft.product.geometry.description || "pending review",
        }),
      });
      const params = withTenant(new URLSearchParams(), tenant);
      await api(`/api/knowledge/products/${draft.id}?${params}`, {
        method: "PUT",
        body: JSON.stringify(draft),
      });
      for (const photo of photos) {
        await uploadFile(draft.id, photo).catch(() => undefined);
      }
      navigate(`/products/${draft.id}`);
    } catch (e) {
      setSaveError(String(e));
    } finally {
      setSaving(false);
    }
  };

  if (draft) {
    return (
      <GlassCard className="form-panel">
        <p className="page-subtitle">Review the AI draft, edit anything that's wrong, then create.</p>
        <ProductForm value={draft} onChange={setDraft} />
        {saveError && <p className="form-error">{saveError}</p>}
        <div className="form-actions">
          <button type="button" className="btn" onClick={() => setDraft(null)}>
            Back
          </button>
          <button type="button" className="btn btn--primary-solid" onClick={onSave} disabled={saving}>
            {saving ? "Creating…" : "Create product"}
          </button>
        </div>
      </GlassCard>
    );
  }

  return (
    <GlassCard className="form-panel">
      <div>
        <label className="field-label" htmlFor="photos-name">
          Product name (optional hint)
        </label>
        <input id="photos-name" className="field-input" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div>
        <label className="field-label" htmlFor="photos-notes">
          Notes for the AI (optional)
        </label>
        <textarea
          id="photos-notes"
          className="field-input field-textarea"
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Anything the photos don't show — dimensions, material, brand marks…"
        />
      </div>
      <div>
        <label className="field-label" htmlFor="photos-files">
          Reference photos
        </label>
        <input
          id="photos-files"
          type="file"
          accept="image/*"
          multiple
          onChange={(e) => setPhotos(Array.from(e.target.files || []))}
        />
        {photos.length > 0 && (
          <p className="page-subtitle" style={{ margin: "0.35rem 0 0" }}>
            {photos.length} photo(s) selected
          </p>
        )}
      </div>
      {extractError && <p className="form-error">{extractError}</p>}
      <div className="form-actions">
        <Link to="/products" className="btn">
          Cancel
        </Link>
        <button type="button" className="btn btn--primary-solid" onClick={onExtract} disabled={photos.length === 0 || extracting}>
          {extracting ? "Extracting…" : "Extract with AI"}
        </button>
      </div>
    </GlassCard>
  );
}

export default function ProductNewPage() {
  const [mode, setMode] = useState<"blank" | "photos">("blank");

  return (
    <div>
      <h1 className="page-title">New product</h1>
      <p className="page-subtitle">Structured ProductSpec — geometry and materials only. No prompts.</p>

      <div className="form-actions" style={{ justifyContent: "flex-start", marginBottom: "0.75rem" }}>
        <button type="button" className={`btn ${mode === "blank" ? "btn--primary-solid" : ""}`} onClick={() => setMode("blank")}>
          Start blank
        </button>
        <button type="button" className={`btn ${mode === "photos" ? "btn--primary-solid" : ""}`} onClick={() => setMode("photos")}>
          Extract from photos
        </button>
      </div>

      {mode === "blank" ? <BlankProductForm /> : <FromPhotosFlow />}
    </div>
  );
}
```

- [ ] **Step 3: Type-check**

Run: `cd ui/app && npx tsc -b --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add ui/app/src/pages/ProductNewPage.tsx ui/app/src/api.ts
git commit -m "feat(ui): new-product-from-photos wizard using AI vision draft + ProductForm review"
```

---

### Task 7: Golden path QA — author `sps` end-to-end in the UI

**Files:** none (manual verification task; no code changes)

**Interfaces:** none — this task exercises Tasks 1-6 together.

- [ ] **Step 1: Start the UI**

Run: `./ui/run.sh`
Expected: server up on `http://localhost:8787`, frontend built/served (or `vite` dev server per `ui/dev.sh` if using that instead).

- [ ] **Step 2: Author `sps` (Shin Pad Sleeve) from scratch using the structured form — no JSON hand-editing**

In the browser: go to `/products/new`, "Start blank", fill:
- Name: `Shin Pad Sleeve`, ID: `shin-pad-sleeve`
- Geometry description: `Black compression sleeve worn over the shin, inside the sock; seamless knit tube`

Create it, land on `/products/shin-pad-sleeve`. In the now-editable form, fill in the remaining fields sourced from `tenants/joga/tools/image-pipeline/jobs/starz-kid-images/sps/cad.md` §1:
- Geometry symmetry: `none`, envelope: `tube`
- Materials: `id=sleeve, type=compression_knit, color=black, finish=matte, texture=seamless_knit`
- Constraints: leave all three checked (preserve geometry/material/proportions)
- Protected regions: `sleeve`; Editable regions: `background`, `scene`

Click Save.

Expected: no error toast; reload `/products/shin-pad-sleeve` and confirm every field you entered is still populated (i.e. it round-tripped through `PUT /api/knowledge/products/shin-pad-sleeve` and `spec_validator_v2.validate_product_spec_v2` without rejection). Inspect `knowledge/products/shin-pad-sleeve.json` on disk and confirm it matches `schemas/product-spec-v2.schema.json` shape (no `geometry_text`, no `prompt_template`).

- [ ] **Step 3: Smoke-test the photo-extraction path (optional, requires a local test image)**

Save any local product-ish photo to `/private/tmp/claude-501/.../scratchpad/test-photo.jpg` (or reuse an existing repo image such as `knowledge/products/hairbands/refs/flatlay.png` if present). Go to `/products/new` → "Extract from photos", upload it, name hint `Test Object`, click "Extract with AI".

Expected: after a short wait, the review form appears pre-filled with a name/geometry/materials draft (not empty, not an error). Edit at least one field, click "Create product", land on the new product's detail page with the photo attached as a reference.

- [ ] **Step 4: Full backend regression + frontend build**

Run: `python3 -m pytest tests/ -q && (cd ui/app && npm run build)`
Expected: all backend tests pass; `tsc -b && vite build` completes without type errors.

- [ ] **Step 5: Commit the QA-created fixture removal (clean up test artifacts, keep `hairbands.json` fixture untouched)**

If `shin-pad-sleeve.json` / the extraction test product were created only for QA and should not ship as a permanent fixture, remove them:

```bash
git status --short knowledge/products/
# if shin-pad-sleeve.json / test-object.json etc. are untracked QA artifacts:
rm -f knowledge/products/shin-pad-sleeve.json knowledge/products/shin-pad-sleeve/refs/* 2>/dev/null
git status --short knowledge/products/
```

If the user wants `shin-pad-sleeve` to stay as a real onboarded product, skip the cleanup and instead:

```bash
git add knowledge/products/shin-pad-sleeve.json
git commit -m "chore(knowledge): onboard shin-pad-sleeve product spec via Product Editor golden path"
```

---

## Self-Review Notes

- **Spec coverage:** Task 1-3 cover `docs/PHASE_2_PIM_PLAN.md`'s "Product editor" scope (all ProductSpec sections: geometry, materials, dimensions, brand/logos, constraints, editing) via `PUT /api/knowledge/products/{slug}` (already existed, no backend change required). Tasks 4-6 cover "wire `core/spec_extractor.py` to a new-product-from-photos UI path." Task 7 is the golden-path acceptance from both `docs/CREATIVE_IDE_UX_PLAN.md` Phase 2 and `docs/PHASE_2_PIM_PLAN.md`.
- **Out of scope (per `docs/PHASE_2_PIM_PLAN.md` "Out of scope" + explicitly not requested):** Campaign editor forms, Supabase `campaigns` mirror migration, Product Explorer tree/outline nav, generic `SchemaForm` renderer. This plan is Product-editor-only, matching the user's literal request.
- **Known architecture gap flagged, not fixed:** per the Explore agent's finding, `tenant.json` only stores `jobs_root`, not a `knowledge_root` — knowledge writes for a tenant land in `workspace/<tenant>/knowledge/products/` inside this repo, not in the tenant's own repo the way jobs do. This plan does not change that (out of scope — it affects all knowledge writes, not just the Product Editor), but it means: when testing Task 7 with a tenant selected in the topbar, expect the new product JSON to land under `workspace/<tenant>/knowledge/products/`, not `tenants/<tenant>/tools/image-pipeline/knowledge/products/`. Test with no tenant selected (repo-root `knowledge/products/`) to match the plan's file paths exactly, or flag this gap to the user if tenant-scoped writes are actually required.
- **Type consistency:** `ProductSpecV2` (Task 1) is used identically in `ProductForm` (Task 2), `ProductDetailPage` (Task 3), and `ProductNewPage` (Task 6) — same field names throughout (`product.geometry.description/symmetry/envelope`, `product.materials[]`, `product.dimensions`, `brand.logos[]`, `constraints.preserve_*`, `editing.protected_regions/editable_regions`). `extract_product_spec_from_photos` (Task 4) and `extractProductFromPhotos`/the `/extract` endpoint (Tasks 5-6) agree on the `{draft: {...}}` response shape.
- **No placeholders:** every step above has runnable code; no "TODO" / "similar to Task N" shortcuts.
