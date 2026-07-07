import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import ArtifactGallery from "../components/ArtifactGallery";
import GlassCard from "../design/GlassCard";
import { api } from "../api";
import { useTenant, withTenant } from "../TenantContext";

type Shot = { id: string; label: string; path: string; dir: string };

type ProductSpec = {
  id: string;
  name: string;
  shots?: Shot[];
  product?: {
    geometry?: { description?: string };
    materials?: { id: string; color?: string; type?: string }[];
  };
  references?: { id: string; role: string; asset: string }[];
  brand?: { logos?: unknown[] };
};

type ProductSource = "knowledge" | "jobs_only";

export default function ProductDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const [product, setProduct] = useState<ProductSpec | null>(null);
  const [source, setSource] = useState<ProductSource | null>(null);
  const [uploading, setUploading] = useState(false);
  const [status, setStatus] = useState("");
  const { tenant } = useTenant();

  const load = () => {
    if (!slug) return;
    const params = withTenant(new URLSearchParams(), tenant);
    api<{ product: ProductSpec; source: ProductSource }>(`/api/products/${slug}?${params}`)
      .then((d) => {
        setProduct(d.product);
        setSource(d.source);
      })
      .catch((e) => setStatus(String(e)));
  };

  useEffect(() => {
    load();
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

  if (!product) {
    return <GlassCard className="stub-note">{status || "Loading…"}</GlassCard>;
  }

  const materials = product.product?.materials || [];
  const refs = product.references || [];
  const isKnowledge = source === "knowledge";

  return (
    <div>
      <Link to="/products" className="btn btn--sm" style={{ marginBottom: "0.75rem", display: "inline-block" }}>
        ← Products
      </Link>
      <h1 className="page-title">{product.name}</h1>
      <p className="page-subtitle">
        {isKnowledge ? `ProductSpec · ${product.id}` : `${product.shots?.length ?? 0} job(s) on disk · ${product.id}`}
      </p>

      {isKnowledge ? (
        <div className="page-grid product-detail-grid" style={{ gap: "1rem" }}>
          <GlassCard className="form-panel">
            <h2 className="dashboard-panel__title">Geometry</h2>
            <p style={{ margin: 0, fontSize: "0.88rem", lineHeight: 1.5 }}>
              {product.product?.geometry?.description}
            </p>
            <h2 className="dashboard-panel__title" style={{ marginTop: "1rem" }}>
              Materials
            </h2>
            <ul className="explorer-list">
              {materials.map((m) => (
                <li key={m.id}>
                  {m.id}: {m.type} · {m.color}
                </li>
              ))}
            </ul>
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
          {product.shots && product.shots.length > 0 && (
            <ul className="explorer-list" style={{ marginTop: "0.75rem" }}>
              {product.shots.map((s) => (
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
        <Link to={`/generate?product=${product.id}`} className="btn btn--primary-solid">
          Generate
        </Link>
      </div>

      <ArtifactGallery productSlug={product.id} title="Recent shots" />
    </div>
  );
}
