import { useEffect, useMemo, useState } from "react";
import GlassCard from "../design/GlassCard";
import { api } from "../api";
import type { Output } from "../types";

export default function AssetsPage() {
  const [outputs, setOutputs] = useState<Output[]>([]);
  const [query, setQuery] = useState("");

  useEffect(() => {
    api<{ products: { slug: string; shots: { dir: string }[] }[] }>("/api/products").then(
      async (d) => {
        const all: Output[] = [];
        for (const p of d.products || []) {
          for (const s of p.shots || []) {
            try {
              const r = await api<{ outputs: Output[] }>(
                `/api/outputs?job_dir=${encodeURIComponent(s.dir)}`
              );
              all.push(...(r.outputs || []));
            } catch {
              /* skip */
            }
          }
        }
        setOutputs(all);
      }
    );
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return outputs;
    return outputs.filter((o) => o.name.toLowerCase().includes(q));
  }, [outputs, query]);

  return (
    <div>
      <h1 className="page-title">Assets</h1>
      <p className="page-subtitle">Outputs and references with lineage (Phase 4).</p>

      <div style={{ marginBottom: "1rem", maxWidth: 320 }}>
        <label htmlFor="assets-search" className="field-label">
          Search assets
        </label>
        <input
          id="assets-search"
          type="search"
          className="field-input"
          placeholder="Filter by filename…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className="assets-grid">
        {filtered.map((o) => (
          <GlassCard key={o.path} className="asset-card">
            <a href={o.url} target="_blank" rel="noopener noreferrer">
              <img src={o.url} alt={o.name} loading="lazy" />
            </a>
            <figcaption>{o.name}</figcaption>
          </GlassCard>
        ))}
      </div>
      {!outputs.length && (
        <GlassCard className="stub-note">No output images yet. Use Generate.</GlassCard>
      )}
      {outputs.length > 0 && !filtered.length && (
        <GlassCard className="stub-note">No assets match “{query}”.</GlassCard>
      )}
    </div>
  );
}
