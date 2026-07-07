import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import ArtifactGallery from "../components/ArtifactGallery";
import GlassCard from "../design/GlassCard";
import { api } from "../api";
import { useTenant, withTenant } from "../TenantContext";
import type { Campaign } from "../types";

export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const { tenant } = useTenant();

  useEffect(() => {
    const params = withTenant(new URLSearchParams(), tenant);
    api<{ campaigns: Campaign[] }>(`/api/campaigns?${params}`).then((d) => setCampaigns(d.campaigns || []));
  }, [tenant]);

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

      <div className="page-grid page-grid--cards">
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
            <div style={{ marginTop: "0.75rem", display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
              <Link to={`/generate?campaign=${c.id}`} className="btn btn--sm">
                Generate with this campaign
              </Link>
              <button
                type="button"
                className="btn btn--sm"
                onClick={() => setExpandedId(expandedId === c.id ? null : c.id)}
              >
                {expandedId === c.id ? "Hide shots" : "Recent shots"}
              </button>
            </div>
            {expandedId === c.id && c.product_spec_id && (
              <ArtifactGallery
                productSlug={c.product_spec_id}
                title={`Outputs · ${c.label}`}
              />
            )}
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
