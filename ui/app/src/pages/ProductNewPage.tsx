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
