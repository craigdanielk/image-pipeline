import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import GlassCard from "../design/GlassCard";
import { api } from "../api";
import type { Job } from "../types";

type RecentRun = Job & {
  mtime: number;
  status: "complete" | "pending";
  outputs: number;
};

type DashboardStats = {
  products: number;
  campaigns: number;
  jobs: number;
  outputs: number;
  credits_estimated: number;
  recent_runs: RecentRun[];
};

const HERO =
  "Turn product and campaign knowledge into shots. Product → Campaign → Generate — zero prompt engineering.";

function formatWhen(ts: number): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function Dashboard() {
  const [stats, setStats] = useState<DashboardStats>({
    products: 0,
    campaigns: 0,
    jobs: 0,
    outputs: 0,
    credits_estimated: 0,
    recent_runs: [],
  });

  useEffect(() => {
    api<DashboardStats>("/api/dashboard/stats")
      .then(setStats)
      .catch(() => {});
  }, []);

  return (
    <div className="dashboard-page">
      <GlassCard className="dashboard-hero glass-card--active">
        <p className="dashboard-hero__eyebrow">Creative IDE</p>
        <h1 className="dashboard-hero__title">Compile knowledge into assets</h1>
        <p className="dashboard-hero__copy">{HERO}</p>
        <div className="dashboard-hero__actions">
          <Link to="/generate" className="btn btn--primary-solid">
            Generate
          </Link>
          <Link to="/products" className="btn">
            Browse products
          </Link>
        </div>
      </GlassCard>

      <div className="page-grid page-grid--stats" style={{ marginTop: "1rem" }}>
        <GlassCard className="stat-card">
          <div className="stat-card__value">{stats.products}</div>
          <div className="stat-card__label">Products</div>
        </GlassCard>
        <GlassCard className="stat-card">
          <div className="stat-card__value">{stats.campaigns}</div>
          <div className="stat-card__label">Campaigns</div>
        </GlassCard>
        <GlassCard className="stat-card">
          <div className="stat-card__value">{stats.jobs}</div>
          <div className="stat-card__label">Runs</div>
        </GlassCard>
        <GlassCard className="stat-card">
          <div className="stat-card__value">{stats.credits_estimated || "—"}</div>
          <div className="stat-card__label">Credits (completed)</div>
        </GlassCard>
        <GlassCard className="stat-card">
          <div className="stat-card__value">{stats.outputs}</div>
          <div className="stat-card__label">Output images</div>
        </GlassCard>
      </div>

      <GlassCard className="dashboard-panel" style={{ marginTop: "1rem" }}>
        <div className="dashboard-panel__header">
          <h2 className="dashboard-panel__title">Recent runs</h2>
          <Link to="/runs" className="btn btn--sm">
            View all
          </Link>
        </div>
        {stats.recent_runs.length === 0 ? (
          <p className="dashboard-empty">No runs yet. Start with Generate.</p>
        ) : (
          <ul className="recent-runs">
            {stats.recent_runs.map((run) => (
              <li key={run.path} className="recent-runs__row">
                <div>
                  <span className="recent-runs__label">{run.label}</span>
                  <span className="recent-runs__meta">
                    {run.product} · {formatWhen(run.mtime)}
                  </span>
                </div>
                <span
                  className={`accent-pill ${run.status === "complete" ? "accent-pill--active" : ""}`}
                >
                  {run.status === "complete" ? `${run.outputs} outputs` : "Pending"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </GlassCard>

      <GlassCard className="dashboard-panel" style={{ marginTop: "1rem" }}>
        <h2 className="dashboard-panel__title">Quick actions</h2>
        <div className="dashboard-actions">
          <Link to="/products" className="btn">
            + Product
          </Link>
          <Link to="/campaigns" className="btn">
            + Campaign
          </Link>
          <Link to="/generate" className="btn btn--primary-solid">
            Generate
          </Link>
          <Link to="/assets" className="btn">
            Assets
          </Link>
        </div>
      </GlassCard>
    </div>
  );
}
