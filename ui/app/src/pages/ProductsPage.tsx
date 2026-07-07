import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import GlassCard from "../design/GlassCard";
import { api } from "../api";
import { useTenant, withTenant } from "../TenantContext";
import type { Product } from "../types";

export default function ProductsPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const { tenant } = useTenant();

  useEffect(() => {
    const params = withTenant(new URLSearchParams(), tenant);
    api<{ products: Product[] }>(`/api/products?${params}`).then((d) => setProducts(d.products || []));
  }, [tenant]);

  return (
    <div>
      <div className="page-header-row">
        <div>
          <h1 className="page-title">Products</h1>
          <p className="page-subtitle">PIM — geometry, materials, logos, constraints. No prompts.</p>
        </div>
        <Link to="/products/new" className="btn btn--primary-solid">
          + New product
        </Link>
      </div>

      <div className="page-grid">
        {products.map((p) => (
          <GlassCard key={p.slug} className="stub-note">
            <Link to={`/products/${p.slug}`} style={{ color: "inherit", textDecoration: "none" }}>
              <strong>{p.name}</strong>
            </Link>
            <p style={{ margin: "0.5rem 0 0", color: "var(--color-text-tertiary)", fontSize: "0.75rem" }}>
              {p.slug}
              {p.source === "knowledge" ? " · knowledge" : p.source === "jobs_only" ? " · legacy jobs" : ""}
            </p>
            <p style={{ margin: "0.35rem 0 0", fontSize: "0.8rem", color: "var(--color-text-secondary)" }}>
              {(p.reference_count ?? 0) > 0 && `${p.reference_count} refs · `}
              {p.shots?.length ?? 0} run{p.shots?.length === 1 ? "" : "s"}
            </p>
          </GlassCard>
        ))}
        {!products.length && (
          <GlassCard className="stub-note dashboard-empty-state">
            <strong>No products yet</strong>
            <p style={{ margin: "0.5rem 0 1rem" }}>
              Start greenfield: create a product, add a campaign, upload refs, then Generate.
            </p>
            <Link to="/products/new" className="btn btn--primary-solid">
              Create first product
            </Link>
          </GlassCard>
        )}
      </div>
    </div>
  );
}
