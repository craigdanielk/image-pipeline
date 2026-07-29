import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import ContextCanvas from "./ContextCanvas";
import GlassNavRail from "./GlassNavRail";
import { useAuth } from "../AuthContext";
import { useCreatorMode } from "../CreatorModeContext";
import { useTenant } from "../TenantContext";
import { useWorkspace } from "../WorkspaceContext";

type Props = {
  children: ReactNode;
};

export default function GlassShell({ children }: Props) {
  const { user, signOut } = useAuth();
  const { creatorMode, toggleMode } = useCreatorMode();
  const { workspace } = useWorkspace();
  const { tenants, tenant, setTenant } = useTenant();

  return (
    <div className="glass-shell">
      <a href="#main-content" className="skip-link sr-only">
        Skip to main content
      </a>
      <ContextCanvas variant="mesh" />
      <div className="glass-shell__frame">
        <GlassNavRail />
        <div className="glass-shell__main">
          <header className="glass-shell__topbar glass-panel">
            <div>
              <span className="glass-shell__product">
                {workspace?.name || "Creative IDE"}
              </span>
            </div>
            <div className="glass-shell__actions">
              {tenants.length > 0 && (
                <select
                  className="field-select glass-shell__tenant"
                  aria-label="Tenant"
                  value={tenant ?? ""}
                  onChange={(e) => setTenant(e.target.value || null)}
                >
                  {tenants.map((t) => (
                    <option key={t.slug} value={t.slug}>
                      {t.label}
                    </option>
                  ))}
                </select>
              )}
              <Link to="/generate" className="btn btn--primary-solid">
                Generate
              </Link>
              <div className="glass-shell__mode">
                <input
                  id="developer-mode-toggle"
                  type="checkbox"
                  checked={!creatorMode}
                  onChange={toggleMode}
                  aria-describedby="developer-mode-hint"
                />
                <label htmlFor="developer-mode-toggle">Developer mode</label>
                <span id="developer-mode-hint" className="sr-only">
                  Shows Runs, Pipelines, and Workbench routes for debugging
                </span>
              </div>

              {/* ── Auth section ── */}
              <div className="glass-shell__user">
                {user && (
                  <span className="glass-shell__user-email" title={user.email ?? ""}>
                    {user.email?.split("@")[0] ?? "User"}
                  </span>
                )}
                <button
                  className="btn btn--ghost btn--sm"
                  onClick={signOut}
                  title="Sign out"
                >
                  Sign out
                </button>
              </div>
            </div>
          </header>
          <main id="main-content" className="glass-shell__content" tabIndex={-1}>
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}
