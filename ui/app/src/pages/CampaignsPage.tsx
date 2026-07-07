import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
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
      <div className="page-header-row">
        <div>
          <h1 className="page-title">Campaigns</h1>
          <p className="page-subtitle">Mood, lighting, camera, palette — CreativeSpec truth. No prompts.</p>
        </div>
        <Link to="/campaigns/new" className="btn btn--primary-solid">
          + New campaign
        </Link>
      </div>

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
            <Link to={`/generate?campaign=${c.id}`} className="btn btn--sm" style={{ marginTop: "0.75rem" }}>
              Generate with this campaign
            </Link>
          </GlassCard>
        ))}
        {!campaigns.length && (
          <GlassCard className="stub-note dashboard-empty-state">
            <strong>No campaigns yet</strong>
            <p style={{ margin: "0.5rem 0 1rem" }}>Create a product first, then add a campaign.</p>
            <Link to="/campaigns/new" className="btn btn--primary-solid">
              Create campaign
            </Link>
          </GlassCard>
        )}
      </div>
    </div>
  );
}
