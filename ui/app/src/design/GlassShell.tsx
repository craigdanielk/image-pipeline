import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import ContextCanvas from "./ContextCanvas";
import GlassNavRail from "./GlassNavRail";
import { useCreatorMode } from "../CreatorModeContext";
import { useWorkspace } from "../WorkspaceContext";

type Props = {
  children: ReactNode;
};

export default function GlassShell({ children }: Props) {
  const { creatorMode, toggleMode } = useCreatorMode();
  const { workspace } = useWorkspace();

  return (
    <div className="glass-shell">
      <a href="#main-content" className="skip-link sr-only">
        Skip to main content
      </a>
      <ContextCanvas />
      <div className="glass-shell__frame">
        <GlassNavRail />
        <div className="glass-shell__main">
          <header className="glass-shell__topbar glass-panel">
            <div>
              <span className="glass-shell__product">
                {workspace?.name || "Creative IDE"}
              </span>
              <span className="glass-shell__tagline">Structured knowledge → deterministic compilation</span>
            </div>
            <div className="glass-shell__actions">
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
