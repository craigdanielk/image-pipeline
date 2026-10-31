// Workspace-scoped Studio home: jobs table with details drawer, exact-version
// library with compare. Membership comes from the authenticated API; the
// remembered workspace is a preference only and is dropped if not a member.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { StudioApi, StudioApiError, type AssetRow, type RunRow, type WorkspaceSummary } from "../StudioApi";
import { RunDrawer } from "../components/RunDrawer";
import { ErrorNotice, StateNotice } from "../components/StateNotice";
import { VariantCompare } from "../components/VariantCompare";

const PREF = "cs.studio.workspace";

export default function StudioListPage() {
  const navigate = useNavigate();
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[] | null>(null);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [assets, setAssets] = useState<AssetRow[]>([]);
  const [error, setError] = useState<StudioApiError | null>(null);
  const [openRun, setOpenRun] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [tab, setTab] = useState<"jobs" | "library">("jobs");
  const [openId, setOpenId] = useState("");

  useEffect(() => {
    const ctrl = new AbortController();
    StudioApi.myWorkspaces(ctrl.signal)
      .then((list) => {
        const member = list.filter((w) => w.role !== null || w.mode === "native");
        setWorkspaces(member);
        const pref = localStorage.getItem(PREF);
        setWorkspaceId(member.find((w) => w.id === pref)?.id ?? member[0]?.id ?? null);
      })
      .catch((e: unknown) => (e as { name?: string }).name !== "AbortError" && setError(e as StudioApiError));
    return () => ctrl.abort();
  }, []);

  useEffect(() => {
    // Scope switch: abort and clear old-scope state before loading the new one.
    setRuns([]);
    setAssets([]);
    setOpenRun(null);
    setPicked([]);
    if (!workspaceId) return;
    localStorage.setItem(PREF, workspaceId);
    const ctrl = new AbortController();
    Promise.all([StudioApi.listRuns(workspaceId, ctrl.signal), StudioApi.listAssets(workspaceId, ctrl.signal)])
      .then(([r, a]) => {
        setRuns(r);
        setAssets(a);
        setError(null);
      })
      .catch((e: unknown) => (e as { name?: string }).name !== "AbortError" && setError(e as StudioApiError));
    return () => ctrl.abort();
  }, [workspaceId]);

  if (error && !workspaces) return <ErrorNotice error={error} />;
  if (!workspaces) return <StateNotice title="Loading workspaces…" />;
  if (!workspaces.length) return <StateNotice title="You are not a member of any Studio workspace" />;
  const pickedAssets = assets.filter((a) => picked.includes(a.id));

  return (
    <div className="cs-page">
      <header className="cs-page-head">
        <h2>Creative Studio</h2>
        <label className="cs-field">
          <span>Workspace</span>
          <select value={workspaceId ?? ""} onChange={(e) => setWorkspaceId(e.target.value)}>
            {workspaces.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name} {w.role ? `(${w.role})` : ""}
              </option>
            ))}
          </select>
        </label>
        <form
          className="cs-actions"
          onSubmit={(e) => {
            e.preventDefault();
            if (workspaceId && /^[0-9a-f-]{36}$/i.test(openId)) navigate(`/studio/${workspaceId}/compositions/${openId}`);
          }}
        >
          <input placeholder="Composition id" value={openId} onChange={(e) => setOpenId(e.target.value.trim())} aria-label="Composition id" />
          <button type="submit">Open layout</button>
        </form>
      </header>
      <ErrorNotice error={error} />
      <nav className="cs-tabs">
        <button type="button" aria-pressed={tab === "jobs"} onClick={() => setTab("jobs")}>
          Jobs ({runs.length})
        </button>
        <button type="button" aria-pressed={tab === "library"} onClick={() => setTab("library")}>
          Library ({assets.length})
        </button>
      </nav>
      {tab === "jobs" ? (
        <table className="cs-table">
          <thead>
            <tr>
              <th>Job</th>
              <th>State</th>
              <th>Cost</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} onClick={() => setOpenRun(r.id)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setOpenRun(r.id)}>
                <td>
                  <code>{r.id.slice(0, 8)}</code>
                </td>
                <td>{r.state}</td>
                <td>{r.cost_state === "unknown" ? "Unknown" : r.cost_state ?? "—"}</td>
                <td>{new Date(r.updated_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <>
          <p className="cs-muted">Select two images to compare exact versions.</p>
          <ul className="cs-library">
            {assets.map((a) => (
              <li key={a.id}>
                <label>
                  <input
                    type="checkbox"
                    disabled={!a.mime_type?.startsWith("image/") || (!picked.includes(a.id) && picked.length >= 2)}
                    checked={picked.includes(a.id)}
                    onChange={(e) => setPicked((p) => (e.target.checked ? [...p, a.id] : p.filter((x) => x !== a.id)))}
                  />{" "}
                  {a.label} <span className="cs-muted">({a.kind}, v{a.revision ?? "—"}{a.latest_version_id ? "" : ", missing file"})</span>
                </label>
              </li>
            ))}
          </ul>
          {workspaceId && pickedAssets.length === 2 ? (
            <VariantCompare workspaceId={workspaceId} left={pickedAssets[0]} right={pickedAssets[1]} />
          ) : null}
        </>
      )}
      {workspaceId && openRun ? <RunDrawer workspaceId={workspaceId} runId={openRun} onClose={() => setOpenRun(null)} /> : null}
    </div>
  );
}
