import { useCallback, useEffect, useState } from "react";
import GlassCard from "../design/GlassCard";
import { api } from "../api";
import type { Artifact } from "../types";

type Props = {
  productSlug?: string;
  campaignSlug?: string;
  title?: string;
};

export default function ArtifactGallery({ productSlug, campaignSlug, title = "Recent shots" }: Props) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [expanded, setExpanded] = useState<Artifact | null>(null);
  const [draftComments, setDraftComments] = useState<Record<string, string>>({});
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setStatus("");
    const params = new URLSearchParams({ limit: "10", sync: "true" });
    if (productSlug) params.set("product_slug", productSlug);
    if (campaignSlug) params.set("campaign_slug", campaignSlug);
    try {
      const data = await api<{ artifacts: Artifact[] }>(`/api/artifacts?${params}`);
      setArtifacts(data.artifacts || []);
      const drafts: Record<string, string> = {};
      for (const a of data.artifacts || []) {
        drafts[a.id] = a.comment || "";
      }
      setDraftComments(drafts);
    } catch (e) {
      setStatus(String(e));
    } finally {
      setLoading(false);
    }
  }, [productSlug, campaignSlug]);

  useEffect(() => {
    load();
  }, [load]);

  const saveComment = async (id: string) => {
    setStatus("");
    try {
      await api(`/api/artifacts/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ comment: draftComments[id] || "" }),
      });
      setStatus("Comment saved");
      load();
    } catch (e) {
      setStatus(String(e));
    }
  };

  const rejectArtifact = async (artifact: Artifact) => {
    const reason = window.prompt(
      "Reject this output? Optional feedback reason:",
      artifact.delete_reason || ""
    );
    if (reason === null) return;
    if (!window.confirm("Reject this output? (soft delete — file stays on disk)")) return;
    setStatus("");
    try {
      const params = new URLSearchParams();
      if (reason) params.set("reason", reason);
      const qs = params.toString();
      await api(`/api/artifacts/${artifact.id}${qs ? `?${qs}` : ""}`, {
        method: "DELETE",
      });
      if (expanded?.id === artifact.id) setExpanded(null);
      load();
    } catch (e) {
      setStatus(String(e));
    }
  };

  if (loading) {
    return (
      <GlassCard className="stub-note" style={{ marginTop: "1rem" }}>
        Loading {title.toLowerCase()}…
      </GlassCard>
    );
  }

  return (
    <div style={{ marginTop: "1rem" }}>
      <h2 className="dashboard-panel__title">{title}</h2>
      <p className="page-subtitle" style={{ marginTop: 0 }}>
        Last 10 outputs — comment or reject as campaign feedback.
      </p>

      {!artifacts.length ? (
        <GlassCard className="stub-note">No artifacts indexed yet. Run a job or sync sheet webps.</GlassCard>
      ) : (
        <div className="assets-grid">
          {artifacts.map((a) => (
            <GlassCard key={a.id} className="asset-card artifact-card">
              <button
                type="button"
                className="artifact-card__thumb"
                onClick={() => setExpanded(a)}
                aria-label={`Expand ${a.name}`}
              >
                <img src={a.url} alt={a.name} loading="lazy" />
              </button>
              <figcaption>
                <span className="artifact-card__name">{a.name}</span>
                <span className={`accent-pill artifact-source artifact-source--${a.source}`}>
                  {a.source.replace("_", " ")}
                </span>
              </figcaption>
              <label className="field-label" htmlFor={`comment-${a.id}`}>
                Comment
              </label>
              <textarea
                id={`comment-${a.id}`}
                className="field-input artifact-comment"
                rows={2}
                value={draftComments[a.id] ?? ""}
                onChange={(e) =>
                  setDraftComments((prev) => ({ ...prev, [a.id]: e.target.value }))
                }
                placeholder="Feedback for this output…"
              />
              <div className="asset-card__actions">
                <button type="button" className="btn btn--sm" onClick={() => saveComment(a.id)}>
                  Save comment
                </button>
                <button
                  type="button"
                  className="btn btn--sm btn--danger"
                  onClick={() => rejectArtifact(a)}
                >
                  Reject output
                </button>
              </div>
            </GlassCard>
          ))}
        </div>
      )}

      {expanded && (
        <div
          className="artifact-modal"
          role="dialog"
          aria-modal="true"
          aria-label={expanded.name}
          onClick={() => setExpanded(null)}
          onKeyDown={(e) => e.key === "Escape" && setExpanded(null)}
        >
          <div className="artifact-modal__inner" onClick={(e) => e.stopPropagation()}>
            <img src={expanded.url} alt={expanded.name} />
            <div className="artifact-modal__meta">
              <strong>{expanded.name}</strong>
              <span>{expanded.source}</span>
              {expanded.comment && <p>{expanded.comment}</p>}
            </div>
            <button type="button" className="btn btn--sm" onClick={() => setExpanded(null)}>
              Close
            </button>
          </div>
        </div>
      )}

      {status && (
        <p role="status" style={{ marginTop: "0.5rem", fontSize: "0.8rem", color: "var(--color-text-tertiary)" }}>
          {status}
        </p>
      )}
    </div>
  );
}
