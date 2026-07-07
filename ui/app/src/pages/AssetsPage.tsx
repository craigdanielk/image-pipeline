import { useEffect, useMemo, useState } from "react";
import GlassCard from "../design/GlassCard";
import { api } from "../api";
import type { Output } from "../types";

export default function AssetsPage() {
  const [outputs, setOutputs] = useState<Output[]>([]);
  const [approvals, setApprovals] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");

  const loadApprovals = () => {
    api<{ approvals: Record<string, string> }>("/api/assets/approvals")
      .then((d) => setApprovals(d.approvals || {}))
      .catch(() => {});
  };

  useEffect(() => {
    loadApprovals();
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

  const setApproval = async (path: string, status: string) => {
    await api(`/api/assets/approval?path=${encodeURIComponent(path)}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
    loadApprovals();
  };

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
            <figcaption>
              {o.name}
              <span className={`accent-pill asset-approval ${approvals[o.path] === "approved" ? "accent-pill--active" : ""}`}>
                {approvals[o.path] || "pending"}
              </span>
            </figcaption>
            <div className="asset-card__actions">
              <button type="button" className="btn btn--sm" onClick={() => setApproval(o.path, "approved")}>
                Approve
              </button>
              <button type="button" className="btn btn--sm" onClick={() => setApproval(o.path, "rejected")}>
                Reject
              </button>
            </div>
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
