import { useState } from "react";
import type { Job, Product } from "./types";
import { useCreatorMode } from "./CreatorModeContext";

type Props = {
  products: Product[];
  jobs: Job[];
  selectedJob: Job | null;
  onSelectJob: (job: Job) => void;
  onNewShot: () => void;
};

export default function ProductSidebar({
  products,
  jobs,
  selectedJob,
  onSelectJob,
  onNewShot,
}: Props) {
  const { creatorMode } = useCreatorMode();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const toggleProduct = (slug: string) => {
    setExpanded((e) => ({ ...e, [slug]: !e[slug] }));
  };

  const findJob = (path: string) => jobs.find((j) => j.path === path);

  if (!creatorMode) {
    return (
      <aside className="sidebar sidebar--dev">
        <h2 className="sidebar__title">Jobs</h2>
        <div className="shot-list">
          {jobs.map((job) => (
            <button
              key={job.path}
              type="button"
              className={`shot-card ${selectedJob?.path === job.path ? "active" : ""}`}
              onClick={() => onSelectJob(job)}
            >
              <span className="shot-card__label">{job.path}</span>
              <span className="shot-card__meta">{job.source}</span>
            </button>
          ))}
          {!jobs.length && <p className="empty">No jobs found</p>}
        </div>
      </aside>
    );
  }

  return (
    <aside className="sidebar">
      <div className="sidebar__header">
        <h2 className="sidebar__title">Products</h2>
        <button type="button" className="btn btn--accent btn--sm" onClick={onNewShot}>
          + New Shot
        </button>
      </div>
      <div className="product-list">
        {products.map((product) => {
          const isOpen = expanded[product.slug] !== false;
          return (
            <div key={product.slug} className="product-group">
              <button
                type="button"
                className="product-group__header"
                onClick={() => toggleProduct(product.slug)}
              >
                <span className="product-group__icon">📦</span>
                <span className="product-group__name">{product.name}</span>
                <span className="product-group__count">{product.shots.length}</span>
                <span className="product-group__chevron">{isOpen ? "▾" : "▸"}</span>
              </button>
              {isOpen && (
                <div className="shot-list">
                  {product.shots.map((shot) => {
                    const job = findJob(shot.path);
                    if (!job) return null;
                    return (
                      <button
                        key={shot.path}
                        type="button"
                        className={`shot-card ${selectedJob?.path === shot.path ? "active" : ""}`}
                        onClick={() => onSelectJob(job)}
                      >
                        <span className="shot-card__label">{shot.label}</span>
                        <span className="shot-card__meta">{shot.id}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
        {!products.length && <p className="empty">No products yet. Create a new shot.</p>}
      </div>
    </aside>
  );
}
