import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import GlassCard from "../design/GlassCard";
import { api } from "../api";
import type { Job } from "../types";

export default function RunsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);

  useEffect(() => {
    api<{ jobs: Job[] }>("/api/jobs").then((d) => setJobs(d.jobs || []));
  }, []);

  return (
    <div>
      <h1 className="page-title">Runs</h1>
      <p className="page-subtitle">DevTools — compile chain, cost, replay, diff (Phase 3+).</p>
      <div className="page-grid">
        {jobs.map((j) => (
          <GlassCard key={j.path} className="stub-note">
            <strong>{j.label}</strong>
            <p style={{ margin: "0.35rem 0", fontSize: "0.75rem", color: "var(--color-text-tertiary)" }}>
              {j.product} · {j.id}
            </p>
            <Link to="/workbench" className="btn btn--sm" style={{ marginTop: "0.5rem" }}>
              Open in Workbench
            </Link>
          </GlassCard>
        ))}
      </div>
    </div>
  );
}
