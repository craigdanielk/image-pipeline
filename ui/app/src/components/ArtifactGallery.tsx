import { useCallback, useEffect, useMemo, useState } from "react";
import BriefModal from "./BriefModal";
import GlassCard from "../design/GlassCard";
import { api } from "../api";
import { useTenant, withTenant } from "../TenantContext";
import type { Artifact, ArtifactStatus } from "../types";

type Props = {
  productSlug?: string;
  campaignSlug?: string;
  title?: string;
};

type FilterTab = "all" | ArtifactStatus;

const PAGE_SIZE = 24;

const FILTER_TABS: { id: FilterTab; label: string }[] = [
  { id: "all", label: "All" },
  { id: "pending", label: "Pending" },
  { id: "approved", label: "Approved" },
  { id: "rejected", label: "Rejected" },
];

const STATUS_LABEL: Record<ArtifactStatus, string> = {
  pending: "Pending",
  approved: "Approved",
  rejected: "Rejected",
};

function relativeTime(unixSeconds: number): string {
  const deltaMs = Date.now() - unixSeconds * 1000;
  const mins = Math.round(deltaMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

export default function ArtifactGallery({ productSlug, campaignSlug, title = "Recent shots" }: Props) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState<FilterTab>("all");
  const [expanded, setExpanded] = useState<Artifact | null>(null);
  const [draftComments, setDraftComments] = useState<Record<string, string>>({});
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [rejectTarget, setRejectTarget] = useState<Artifact | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [regenTarget, setRegenTarget] = useState<Artifact | null>(null);
  const { tenant } = useTenant();

  const buildParams = useCallback(
    (offset: number) => {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset), sync: offset === 0 ? "true" : "false" });
      if (productSlug) params.set("product_slug", productSlug);
      if (campaignSlug) params.set("campaign_slug", campaignSlug);
      if (filter === "rejected") params.set("status", "rejected");
      else if (filter === "approved") params.set("status", "approved");
      else if (filter === "pending") params.set("status", "pending");
      // "All" = pending + approved. Rejected stays in its own tab only.
      return withTenant(params, tenant);
    },
    [productSlug, campaignSlug, filter, tenant]
  );

  const load = useCallback(async () => {
    setLoading(true);
    setStatus("");
    try {
      const data = await api<{ artifacts: Artifact[]; total: number }>(`/api/artifacts?${buildParams(0)}`);
      setArtifacts(data.artifacts || []);
      setTotal(data.total ?? (data.artifacts || []).length);
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
  }, [buildParams]);

  const loadMore = async () => {
    setLoadingMore(true);
    try {
      const data = await api<{ artifacts: Artifact[]; total: number }>(`/api/artifacts?${buildParams(artifacts.length)}`);
      setArtifacts((prev) => [...prev, ...(data.artifacts || [])]);
      setTotal(data.total ?? total);
      setDraftComments((prev) => {
        const next = { ...prev };
        for (const a of data.artifacts || []) next[a.id] = a.comment || "";
        return next;
      });
    } catch (e) {
      setStatus(String(e));
    } finally {
      setLoadingMore(false);
    }
  };

  useEffect(() => {
    load();
  }, [load]);

  // Escape-to-close + focus the modal so keyboard users can dismiss it.
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  // Update one row in place from an API response — never reload the whole
  // gallery for a single action (a full load() flips `loading` and unmounts
  // the list, which resets the page scroll to the top).
  const applyRow = useCallback(
    (row: Artifact) => {
      const matchesFilter = filter === "all" || row.status === filter;
      setArtifacts((prev) => {
        if (!matchesFilter) {
          setTotal((t) => Math.max(0, t - 1));
          return prev.filter((a) => a.id !== row.id);
        }
        return prev.map((a) => (a.id === row.id ? row : a));
      });
    },
    [filter]
  );

  const saveComment = async (id: string) => {
    setStatus("");
    try {
      const params = withTenant(new URLSearchParams(), tenant);
      const data = await api<{ artifact: Artifact }>(`/api/artifacts/${id}?${params}`, {
        method: "PATCH",
        body: JSON.stringify({ comment: draftComments[id] || "" }),
      });
      if (data.artifact) applyRow(data.artifact);
      setStatus("Comment saved");
    } catch (e) {
      setStatus(String(e));
    }
  };

  const approveArtifact = async (artifact: Artifact) => {
    setStatus("");
    try {
      const params = withTenant(new URLSearchParams(), tenant);
      const data = await api<{ artifact: Artifact }>(`/api/artifacts/${artifact.id}/approve?${params}`, { method: "POST" });
      if (data.artifact) applyRow(data.artifact);
    } catch (e) {
      setStatus(String(e));
    }
  };

  const restoreArtifact = async (artifact: Artifact) => {
    setStatus("");
    try {
      const params = withTenant(new URLSearchParams(), tenant);
      const data = await api<{ artifact: Artifact }>(`/api/artifacts/${artifact.id}/restore?${params}`, { method: "POST" });
      if (data.artifact) applyRow(data.artifact);
    } catch (e) {
      setStatus(String(e));
    }
  };

  const confirmReject = async () => {
    if (!rejectTarget) return;
    setStatus("");
    try {
      const params = withTenant(new URLSearchParams(), tenant);
      if (rejectReason.trim()) params.set("reason", rejectReason.trim());
      const qs = params.toString();
      const data = await api<{ artifact: Artifact }>(`/api/artifacts/${rejectTarget.id}${qs ? `?${qs}` : ""}`, { method: "DELETE" });
      if (expanded?.id === rejectTarget.id) setExpanded(null);
      setRejectTarget(null);
      setRejectReason("");
      if (data.artifact) applyRow(data.artifact);
    } catch (e) {
      setStatus(String(e));
    }
  };

  // One flat grid: cards sorted by shot group then name so versions of the
  // same shot sit adjacent, and rows fill naturally as images are added.
  const sorted = useMemo(
    () =>
      [...artifacts].sort(
        (a, b) => a.group_key.localeCompare(b.group_key) || a.name.localeCompare(b.name)
      ),
    [artifacts]
  );

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
        {total} output{total === 1 ? "" : "s"} — approve, comment, or reject as campaign feedback.
      </p>

      <div className="chip-grid" style={{ marginBottom: "0.85rem" }}>
        {FILTER_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`chip ${filter === tab.id ? "chip--on" : ""}`}
            onClick={() => setFilter(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {!artifacts.length ? (
        <GlassCard className="stub-note">No {filter === "all" ? "" : filter} artifacts here yet.</GlassCard>
      ) : (
        <>
          <div className="assets-grid">
            {sorted.map((a) => (
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
                      <span className="artifact-card__name" title={a.name}>{a.name}</span>
                      <div className="artifact-card__pills">
                        <span className={`accent-pill artifact-status artifact-status--${a.status}`}>
                          {STATUS_LABEL[a.status]}
                        </span>
                        <span className={`accent-pill artifact-source artifact-source--${a.source}`}>
                          {a.source.replace("_", " ")}
                        </span>
                        <span className="artifact-card__time">{relativeTime(a.created_at)}</span>
                      </div>
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
                        Save
                      </button>
                      {a.status === "rejected" ? (
                        <button type="button" className="btn btn--sm" onClick={() => restoreArtifact(a)}>
                          Restore
                        </button>
                      ) : (
                        <>
                          <button
                            type="button"
                            className="btn btn--sm btn--approve"
                            onClick={() => approveArtifact(a)}
                            disabled={a.status === "approved"}
                          >
                            Approve
                          </button>
                          <button
                            type="button"
                            className="btn btn--sm btn--danger"
                            onClick={() => {
                              setRejectTarget(a);
                              setRejectReason(a.delete_reason || "");
                            }}
                          >
                            Reject
                          </button>
                        </>
                      )}
                      <button type="button" className="btn btn--sm" onClick={() => setRegenTarget(a)}>
                        Regenerate
                      </button>
                      {a.drive_url && (
                        <a
                          className="btn btn--sm"
                          href={a.drive_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          title="Open the uploaded copy on Google Drive"
                        >
                          Drive ↗
                        </a>
                      )}
                    </div>
              </GlassCard>
            ))}
          </div>

          {artifacts.length < total && (
            <div style={{ display: "flex", justifyContent: "center", marginTop: "1rem" }}>
              <button type="button" className="btn" onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? "Loading…" : `Load more (${artifacts.length}/${total})`}
              </button>
            </div>
          )}
        </>
      )}

      {expanded && (
        <div
          className="artifact-modal"
          role="dialog"
          aria-modal="true"
          aria-label={expanded.name}
          tabIndex={-1}
          onClick={() => setExpanded(null)}
        >
          <div className="artifact-modal__inner" onClick={(e) => e.stopPropagation()}>
            <img src={expanded.url} alt={expanded.name} />
            <div className="artifact-modal__meta">
              <strong>{expanded.name}</strong>
              <span>
                {STATUS_LABEL[expanded.status]} · {expanded.source} · {relativeTime(expanded.created_at)}
              </span>
              {expanded.comment && <p>{expanded.comment}</p>}
              {expanded.delete_reason && <p>Rejected: {expanded.delete_reason}</p>}
            </div>
            <button type="button" className="btn btn--sm" onClick={() => setExpanded(null)}>
              Close
            </button>
          </div>
        </div>
      )}

      {rejectTarget && (
        <div className="artifact-modal" role="dialog" aria-modal="true" aria-label="Reject output" onClick={() => setRejectTarget(null)}>
          <div className="artifact-modal__inner" onClick={(e) => e.stopPropagation()}>
            <strong>Reject {rejectTarget.name}?</strong>
            <p className="page-subtitle" style={{ margin: "0.5rem 0" }}>
              File stays on disk — this only hides it from the default view.
            </p>
            <label className="field-label" htmlFor="reject-reason">
              Reason (optional)
            </label>
            <textarea
              id="reject-reason"
              className="field-input"
              rows={3}
              autoFocus
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="Why is this getting rejected?"
            />
            <div className="asset-card__actions" style={{ marginTop: "0.75rem" }}>
              <button type="button" className="btn btn--sm" onClick={() => setRejectTarget(null)}>
                Cancel
              </button>
              <button type="button" className="btn btn--sm btn--danger" onClick={confirmReject}>
                Confirm reject
              </button>
            </div>
          </div>
        </div>
      )}

      {regenTarget && (
        <BriefModal
          productSlug={regenTarget.product_slug}
          basedOnLabel={`Regenerating from: ${regenTarget.name} (${regenTarget.status})`}
          initialFeedback={regenTarget.delete_reason || regenTarget.comment || undefined}
          onClose={() => setRegenTarget(null)}
          onGenerated={load}
        />
      )}

      {status && (
        <p role="status" style={{ marginTop: "0.5rem", fontSize: "0.8rem", color: "var(--color-text-tertiary)" }}>
          {status}
        </p>
      )}
    </div>
  );
}
