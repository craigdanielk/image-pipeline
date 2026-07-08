import { useEffect, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
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
  const location = useLocation();
  const [product, setProduct] = useState<ProductSpecV2 | JobsOnlyProduct | null>(null);
  const [source, setSource] = useState<ProductSource | null>(null);
  const [draft, setDraft] = useState<ProductSpecV2 | null>(null);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [status, setStatus] = useState(
    () => (location.state as { warning?: string } | null)?.warning || ""
  );
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
