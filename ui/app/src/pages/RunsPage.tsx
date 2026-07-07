import { useEffect, useState } from "react";
import GlassCard from "../design/GlassCard";
import { api } from "../api";

type RegistryRun = {
  id: string;
  status: string;
  product_slug: string;
  campaign_id: string;
  shot_id: string;
  job_path: string;
  outputs: { name: string; path: string; url: string }[];
  credits_estimated?: number;
  error?: string;
  created_at: number;
  updated_at: number;
};

function formatWhen(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const STATUS_CLASS: Record<string, string> = {
  complete: "accent-pill--active",
  running: "",
  queued: "",
  failed: "",
};

export default function RunsPage() {
  const [runs, setRuns] = useState<RegistryRun[]>([]);

  useEffect(() => {
    api<{ runs: RegistryRun[] }>("/api/runs/registry").then((d) => setRuns(d.runs || []));
    const t = setInterval(() => {
      api<{ runs: RegistryRun[] }>("/api/runs/registry").then((d) => setRuns(d.runs || []));
    }, 5000);
    return () => clearInterval(t);
  }, []);

  return (
    <div>
      <h1 className="page-title">Runs</h1>
      <p className="page-subtitle">Execution queue — status, outputs, lineage.</p>
      <div className="page-grid page-grid--cards">
        {runs.map((r) => (
          <GlassCard key={r.id} className="stub-note">
            <div style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem" }}>
              <strong>{r.shot_id}</strong>
              <span className={`accent-pill ${STATUS_CLASS[r.status] || ""}`}>{r.status}</span>
            </div>
            <p style={{ margin: "0.35rem 0", fontSize: "0.75rem", color: "var(--color-text-tertiary)" }}>
              {r.product_slug} · {r.campaign_id} · {formatWhen(r.updated_at || r.created_at)}
            </p>
            {r.error && (
              <p style={{ margin: "0.35rem 0", fontSize: "0.75rem", color: "var(--color-status-error)" }}>
                {r.error}
              </p>
            )}
            <p style={{ margin: "0.35rem 0 0", fontSize: "0.8rem" }}>
              {r.outputs?.length ?? 0} output{(r.outputs?.length ?? 0) === 1 ? "" : "s"}
            </p>
          </GlassCard>
        ))}
        {!runs.length && (
          <GlassCard className="stub-note">
            No runs yet. Use Generate to queue your first compilation.
          </GlassCard>
        )}
      </div>
    </div>
  );
}
