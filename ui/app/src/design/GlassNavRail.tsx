import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { useCreatorMode } from "../CreatorModeContext";

type NavItem = {
  to: string;
  icon: ReactNode;
  label: string;
  devOnly?: boolean;
};

/* 16px stroke icons, currentColor — no emoji anywhere in the chrome. */
const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const Icon = {
  dashboard: (
    <svg viewBox="0 0 24 24" {...stroke}>
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </svg>
  ),
  products: (
    <svg viewBox="0 0 24 24" {...stroke}>
      <path d="M21 8.5v7a2 2 0 0 1-1 1.73l-6 3.5a2 2 0 0 1-2 0l-6-3.5A2 2 0 0 1 5 15.5v-7a2 2 0 0 1 1-1.73l6-3.5a2 2 0 0 1 2 0l6 3.5a2 2 0 0 1 1 1.73Z" />
      <path d="M5.3 7.3 13 11.7l7.7-4.4" />
      <path d="M13 22V11.7" />
    </svg>
  ),
  campaigns: (
    <svg viewBox="0 0 24 24" {...stroke}>
      <circle cx="13.5" cy="6.5" r="0.5" />
      <circle cx="17.5" cy="10.5" r="0.5" />
      <circle cx="8.5" cy="7.5" r="0.5" />
      <circle cx="6.5" cy="12.5" r="0.5" />
      <path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.93 0 1.68-.75 1.68-1.68 0-.44-.16-.84-.44-1.14-.27-.3-.43-.7-.43-1.13a1.68 1.68 0 0 1 1.68-1.68h1.98A5.53 5.53 0 0 0 22 10.85C22 5.95 17.5 2 12 2Z" />
    </svg>
  ),
  assets: (
    <svg viewBox="0 0 24 24" {...stroke}>
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <circle cx="9" cy="9" r="2" />
      <path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21" />
    </svg>
  ),
  generate: (
    <svg viewBox="0 0 24 24" {...stroke}>
      <path d="M13 2 4.1 12.7c-.3.4 0 .9.5.9H11l-1 8.4 8.9-10.7c.3-.4 0-.9-.5-.9H13l1-8.4Z" />
    </svg>
  ),
  runs: (
    <svg viewBox="0 0 24 24" {...stroke}>
      <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
    </svg>
  ),
  pipelines: (
    <svg viewBox="0 0 24 24" {...stroke}>
      <circle cx="6" cy="6" r="3" />
      <circle cx="18" cy="18" r="3" />
      <path d="M6 9v3a3 3 0 0 0 3 3h6" />
    </svg>
  ),
  knowledge: (
    <svg viewBox="0 0 24 24" {...stroke}>
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z" />
    </svg>
  ),
  providers: (
    <svg viewBox="0 0 24 24" {...stroke}>
      <path d="M12 22v-5" />
      <path d="M9 8V2" />
      <path d="M15 8V2" />
      <path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z" />
    </svg>
  ),
  workbench: (
    <svg viewBox="0 0 24 24" {...stroke}>
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
    </svg>
  ),
  settings: (
    <svg viewBox="0 0 24 24" {...stroke}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
    </svg>
  ),
};

const NAV: NavItem[] = [
  { to: "/", icon: Icon.dashboard, label: "Dashboard" },
  { to: "/products", icon: Icon.products, label: "Products" },
  { to: "/campaigns", icon: Icon.campaigns, label: "Campaigns" },
  { to: "/assets", icon: Icon.assets, label: "Assets" },
  { to: "/generate", icon: Icon.generate, label: "Generate" },
  { to: "/runs", icon: Icon.runs, label: "Runs" },
  { to: "/pipelines", icon: Icon.pipelines, label: "Pipelines", devOnly: true },
  { to: "/knowledge", icon: Icon.knowledge, label: "Knowledge", devOnly: true },
  { to: "/providers", icon: Icon.providers, label: "Providers", devOnly: true },
  { to: "/workbench", icon: Icon.workbench, label: "Workbench", devOnly: true },
  { to: "/settings", icon: Icon.settings, label: "Settings", devOnly: true },
];

export default function GlassNavRail() {
  const { creatorMode } = useCreatorMode();
  const items = NAV.filter((n) => !n.devOnly || !creatorMode);

  return (
    <nav className="glass-nav-rail glass-panel" aria-label="Main">
      <div className="glass-nav-rail__brand" title="Creative IDE">
        <span className="glass-nav-rail__logo" aria-hidden="true">
          <svg viewBox="0 0 24 24" {...stroke} strokeWidth={2}>
            <path d="M12 2 22 12 12 22 2 12Z" />
          </svg>
        </span>
        <span className="glass-nav-rail__wordmark">Creative IDE</span>
      </div>
      <ul className="glass-nav-rail__list">
        {items.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `glass-nav-rail__link ${isActive ? "glass-nav-rail__link--active" : ""}`
              }
              title={item.label}
            >
              <span className="glass-nav-rail__icon">{item.icon}</span>
              <span className="glass-nav-rail__label">{item.label}</span>
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
