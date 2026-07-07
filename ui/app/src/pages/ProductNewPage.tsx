import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import GlassCard from "../design/GlassCard";
import { api } from "../api";

export default function ProductNewPage() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [geometry, setGeometry] = useState("");
  const [materialColor, setMaterialColor] = useState("neutral");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const slugify = (s: string) =>
    s
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "");

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
    <div>
      <h1 className="page-title">New product</h1>
      <p className="page-subtitle">Structured ProductSpec — geometry and materials only. No prompts.</p>

      <GlassCard className="form-panel">
        <form onSubmit={onSubmit} className="structured-form">
          <div>
            <label htmlFor="product-name" className="field-label">
              Product name
            </label>
            <input
              id="product-name"
              className="field-input"
              value={name}
              onChange={(e) => onName(e.target.value)}
              required
            />
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
            <input
              id="product-color"
              className="field-input"
              value={materialColor}
              onChange={(e) => setMaterialColor(e.target.value)}
            />
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
    </div>
  );
}
