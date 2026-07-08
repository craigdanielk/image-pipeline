import { useEffect, useState } from "react";
import ArtifactGallery from "../components/ArtifactGallery";
import { api } from "../api";
import { useTenant, withTenant } from "../TenantContext";

type ProductRow = { slug: string };

export default function AssetsPage() {
  const [products, setProducts] = useState<ProductRow[]>([]);
  const [product, setProduct] = useState("");
  const { tenant } = useTenant();

  useEffect(() => {
    const params = withTenant(new URLSearchParams(), tenant);
    api<{ products: ProductRow[] }>(`/api/products?${params}`)
      .then((d) => setProducts(d.products || []))
      .catch(() => setProducts([]));
  }, [tenant]);

  return (
    <div>
      <h1 className="page-title">Assets</h1>
      <p className="page-subtitle">
        Artifact registry — every render across products. Approve, comment, or reject; rejected files move out of the deliverable set.
      </p>

      <div style={{ marginBottom: "1rem", maxWidth: 320 }}>
        <label htmlFor="assets-product" className="field-label">
          Product
        </label>
        <select
          id="assets-product"
          className="field-input"
          value={product}
          onChange={(e) => setProduct(e.target.value)}
        >
          <option value="">All products</option>
          {products.map((p) => (
            <option key={p.slug} value={p.slug}>
              {p.slug}
            </option>
          ))}
        </select>
      </div>

      <ArtifactGallery key={product || "all"} productSlug={product || undefined} title="All artifacts" />
    </div>
  );
}
