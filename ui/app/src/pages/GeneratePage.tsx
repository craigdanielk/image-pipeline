import { useEffect, useState } from "react";
import GlassCard from "../design/GlassCard";
import { api } from "../api";
import type { Campaign, Operation, Product } from "../types";

const OUTPUT_TYPES = [
  { id: "pdp", label: "PDP" },
  { id: "lifestyle", label: "Lifestyle" },
  { id: "hero", label: "Hero" },
  { id: "social", label: "Social" },
];

export default function GeneratePage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [productSlug, setProductSlug] = useState("");
  const [campaignId, setCampaignId] = useState("");
  const [outputs, setOutputs] = useState<Set<string>>(new Set(["hero"]));
  const [changes, setChanges] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(false);
  const [credits, setCredits] = useState<number | null>(null);

  useEffect(() => {
    Promise.all([
      api<{ products: Product[] }>("/api/products"),
      api<{ campaigns: Campaign[] }>("/api/campaigns"),
      api<{ operations: Operation[] }>("/api/operations"),
    ]).then(([p, c, o]) => {
      setProducts(p.products || []);
      setCampaigns(c.campaigns || []);
      setOperations(o.operations || []);
      if (p.products?.[0]) setProductSlug(p.products[0].slug);
      if (c.campaigns?.[0]) setCampaignId(c.campaigns[0].id);
    });
  }, []);

  const toggle = (set: Set<string>, id: string, setter: (s: Set<string>) => void) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setter(next);
  };

  const onEstimate = async () => {
    if (!productSlug || !campaignId || outputs.size === 0) return;
    setLoading(true);
    setStatus("Estimating…");
    try {
      const data = await api<{ credits?: number; message?: string }>("/api/generate/intent", {
        method: "POST",
        body: JSON.stringify({
          product_slug: productSlug,
          campaign_id: campaignId,
          output_types: [...outputs],
          changes: [...changes],
          dry_run: true,
        }),
      });
      setCredits(data.credits ?? null);
      setStatus(data.message || "Estimate ready");
    } catch (e) {
      setStatus(String(e));
    } finally {
      setLoading(false);
    }
  };

  const onGenerate = async () => {
    if (!productSlug || !campaignId || outputs.size === 0) return;
    const note = credits != null ? ` (~${credits} credits)` : "";
    if (!confirm(`Generate${note}? Compiler will assemble prompts — you won't edit them.`)) return;
    setLoading(true);
    setStatus("Compiling and running…");
    try {
      const data = await api<{ message?: string; job_path?: string }>("/api/generate/intent", {
        method: "POST",
        body: JSON.stringify({
          product_slug: productSlug,
          campaign_id: campaignId,
          output_types: [...outputs],
          changes: [...changes],
          dry_run: false,
        }),
      });
      setStatus(data.message || `Created ${data.job_path}`);
    } catch (e) {
      setStatus(String(e));
    } finally {
      setLoading(false);
    }
  };

  const filteredCampaigns = campaigns.filter(
    (c) => !productSlug || c.product_spec_id === productSlug || !c.product_spec_id
  );

  return (
    <div className="generate-page">
      <h1 className="page-title">Generate</h1>
      <p className="page-subtitle">Intent only — compiler owns prompts. Zero prompt boxes.</p>

      <GlassCard className="generate-panel">
        <div className="generate-panel__grid">
          <div>
            <label htmlFor="generate-product" className="field-label">
              Product
            </label>
            <select
              id="generate-product"
              className="field-select"
              value={productSlug}
              onChange={(e) => setProductSlug(e.target.value)}
            >
              <option value="">Select…</option>
              {products.map((p) => (
                <option key={p.slug} value={p.slug}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="generate-campaign" className="field-label">
              Campaign
            </label>
            <select
              id="generate-campaign"
              className="field-select"
              value={campaignId}
              onChange={(e) => setCampaignId(e.target.value)}
            >
              <option value="">Select…</option>
              {filteredCampaigns.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <fieldset className="field-group" style={{ marginTop: "1.25rem" }}>
          <legend className="field-label">Output types</legend>
          <div className="chip-grid" role="group" aria-label="Output types">
            {OUTPUT_TYPES.map((o) => (
              <button
                key={o.id}
                type="button"
                className={`chip ${outputs.has(o.id) ? "chip--on" : ""}`}
                aria-pressed={outputs.has(o.id)}
                onClick={() => toggle(outputs, o.id, setOutputs)}
              >
                {o.label}
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset className="field-group" style={{ marginTop: "1.25rem" }}>
          <legend className="field-label">Changes</legend>
          <div className="chip-grid" role="group" aria-label="Changes">
            {operations.map((op) => (
              <button
                key={op.id}
                type="button"
                className={`chip ${changes.has(op.id) ? "chip--on" : ""}`}
                aria-pressed={changes.has(op.id)}
                onClick={() => toggle(changes, op.id, setChanges)}
              >
                + {op.label}
              </button>
            ))}
          </div>
        </fieldset>

        <div className="generate-panel__footer">
          {credits != null && <span className="accent-pill">~{credits} credits</span>}
          <span
            role="status"
            aria-live="polite"
            style={{ flex: 1, fontSize: "0.8rem", color: "var(--color-text-tertiary)" }}
          >
            {status}
          </span>
          <button type="button" className="btn" disabled={loading} onClick={onEstimate}>
            Estimate
          </button>
          <button
            type="button"
            className="btn btn--primary-solid"
            disabled={loading || !productSlug || !campaignId}
            onClick={onGenerate}
          >
            Generate
          </button>
        </div>
      </GlassCard>
    </div>
  );
}
