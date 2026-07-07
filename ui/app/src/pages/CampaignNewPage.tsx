import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import GlassCard from "../design/GlassCard";
import { api } from "../api";
import type { Product } from "../types";

export default function CampaignNewPage() {
  const navigate = useNavigate();
  const [products, setProducts] = useState<Product[]>([]);
  const [productSpecId, setProductSpecId] = useState("");
  const [label, setLabel] = useState("");
  const [slug, setSlug] = useState("");
  const [mood, setMood] = useState("studio");
  const [sceneDescription, setSceneDescription] = useState("");
  const [lighting, setLighting] = useState("soft_key_fill, neutral backdrop");
  const [camera, setCamera] = useState("eye_level, product legible");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api<{ products: Product[] }>("/api/products").then((d) => {
      setProducts(d.products || []);
      if (d.products?.[0]) setProductSpecId(d.products[0].slug);
    });
  }, []);

  const slugify = (s: string) =>
    s
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "");

  const onLabel = (v: string) => {
    setLabel(v);
    if (!slug || slug === slugify(label)) setSlug(slugify(v));
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      await api("/api/knowledge/campaigns", {
        method: "POST",
        body: JSON.stringify({
          id: slug || slugify(label),
          product_spec_id: productSpecId,
          label,
          mood,
          scene_description: sceneDescription,
          lighting,
          camera,
        }),
      });
      navigate("/campaigns");
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <h1 className="page-title">New campaign</h1>
      <p className="page-subtitle">CreativeSpec — scene, lighting, camera. Compiler owns prompts.</p>

      <GlassCard className="form-panel">
        <form onSubmit={onSubmit} className="structured-form">
          <div>
            <label htmlFor="campaign-product" className="field-label">
              Product
            </label>
            <select
              id="campaign-product"
              className="field-select"
              value={productSpecId}
              onChange={(e) => setProductSpecId(e.target.value)}
              required
            >
              <option value="">Select product…</option>
              {products.map((p) => (
                <option key={p.slug} value={p.slug}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="campaign-label" className="field-label">
              Campaign name
            </label>
            <input
              id="campaign-label"
              className="field-input"
              value={label}
              onChange={(e) => onLabel(e.target.value)}
              required
            />
          </div>
          <div>
            <label htmlFor="campaign-slug" className="field-label">
              Campaign ID
            </label>
            <input
              id="campaign-slug"
              className="field-input"
              value={slug}
              onChange={(e) => setSlug(slugify(e.target.value))}
              required
            />
          </div>
          <div>
            <label htmlFor="campaign-mood" className="field-label">
              Mood
            </label>
            <input
              id="campaign-mood"
              className="field-input"
              value={mood}
              onChange={(e) => setMood(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="campaign-scene" className="field-label">
              Scene description
            </label>
            <textarea
              id="campaign-scene"
              className="field-input field-textarea"
              rows={3}
              value={sceneDescription}
              onChange={(e) => setSceneDescription(e.target.value)}
              required
            />
          </div>
          <div>
            <label htmlFor="campaign-lighting" className="field-label">
              Lighting
            </label>
            <input
              id="campaign-lighting"
              className="field-input"
              value={lighting}
              onChange={(e) => setLighting(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="campaign-camera" className="field-label">
              Camera
            </label>
            <input
              id="campaign-camera"
              className="field-input"
              value={camera}
              onChange={(e) => setCamera(e.target.value)}
            />
          </div>
          {error && <p className="form-error">{error}</p>}
          <div className="form-actions">
            <Link to="/campaigns" className="btn">
              Cancel
            </Link>
            <button type="submit" className="btn btn--primary-solid" disabled={loading || !productSpecId}>
              Create campaign
            </button>
          </div>
        </form>
      </GlassCard>
    </div>
  );
}
