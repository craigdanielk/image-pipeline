import { NavLink } from "react-router-dom";
import { useCreatorMode } from "../CreatorModeContext";

type NavItem = {
  to: string;
  icon: string;
  label: string;
  devOnly?: boolean;
};

const NAV: NavItem[] = [
  { to: "/", icon: "🏠", label: "Dashboard" },
  { to: "/products", icon: "📦", label: "Products" },
  { to: "/campaigns", icon: "🎨", label: "Campaigns" },
  { to: "/assets", icon: "🖼", label: "Assets" },
  { to: "/generate", icon: "⚡", label: "Generate" },
  { to: "/runs", icon: "📈", label: "Runs" },
  { to: "/pipelines", icon: "🌳", label: "Pipelines", devOnly: true },
  { to: "/knowledge", icon: "🧠", label: "Knowledge", devOnly: true },
  { to: "/providers", icon: "🔌", label: "Providers", devOnly: true },
  { to: "/workbench", icon: "🔧", label: "Workbench", devOnly: true },
  { to: "/settings", icon: "⚙", label: "Settings", devOnly: true },
];

export default function GlassNavRail() {
  const { creatorMode } = useCreatorMode();
  const items = NAV.filter((n) => !n.devOnly || !creatorMode);

  return (
    <nav className="glass-nav-rail glass-panel" aria-label="Main">
      <div className="glass-nav-rail__brand" title="Creative IDE">
        <span className="glass-nav-rail__logo">◇</span>
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
