import { useEffect, useState } from "react";
import GlassCard from "../design/GlassCard";
import { api } from "../api";
import type { Campaign } from "../types";

export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);

  useEffect(() => {
    api<{ campaigns: Campaign[] }>("/api/campaigns").then((d) => setCampaigns(d.campaigns || []));
  }, []);

  return (
    <div>
      <h1 className="page-title">Campaigns</h1>
      <p className="page-subtitle">Mood, lighting, camera, palette — CreativeSpec truth. No prompts.</p>
      <div className="page-grid">
        {campaigns.map((c) => (
          <GlassCard key={c.id} className="stub-note">
            <strong>{c.label}</strong>
            <p style={{ margin: "0.35rem 0 0", fontSize: "0.8rem", color: "var(--color-text-secondary)" }}>
              Product: {c.product_spec_id}
            </p>
            {c.scene?.lighting && (
              <p style={{ margin: "0.5rem 0 0", fontSize: "0.75rem" }}>
                Lighting: {c.scene.lighting}
              </p>
            )}
          </GlassCard>
        ))}
        {!campaigns.length && (
          <GlassCard className="stub-note">Add campaigns under <code>knowledge/campaigns/</code> (Phase 2: UI editor).</GlassCard>
        )}
      </div>
    </div>
  );
}
