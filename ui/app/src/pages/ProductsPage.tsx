import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import GlassCard from "../design/GlassCard";
import { api } from "../api";
import type { Product } from "../types";

export default function ProductsPage() {
  const [products, setProducts] = useState<Product[]>([]);

  useEffect(() => {
    api<{ products: Product[] }>("/api/products").then((d) => setProducts(d.products || []));
  }, []);

  return (
    <div>
      <h1 className="page-title">Products</h1>
      <p className="page-subtitle">PIM — geometry, materials, logos, constraints. No prompts.</p>
      <div className="page-grid">
        {products.map((p) => (
          <GlassCard key={p.slug} className="stub-note">
            <strong>{p.name}</strong>
            <p style={{ margin: "0.5rem 0 0", color: "var(--color-text-tertiary)" }}>
              {p.shots.length} shot{p.shots.length !== 1 ? "s" : ""}
            </p>
            <p style={{ margin: "0.75rem 0 0", fontSize: "0.75rem" }}>
              Phase 2: Product explorer (geometry, versions, assets)
            </p>
          </GlassCard>
        ))}
        {!products.length && (
          <GlassCard className="stub-note">No products yet. Use Generate or add jobs under <code>jobs/</code>.</GlassCard>
        )}
      </div>
      <Link to="/generate" className="btn btn--primary" style={{ marginTop: "1rem", display: "inline-block" }}>
        Generate from product
      </Link>
    </div>
  );
}
