// Artboard / layer / preview / inspector workspace for one saved composition.
import { useState } from "react";
import { useParams } from "react-router-dom";
import { saveStatusLabel } from "../RecipeSession";
import { StudioApi, type ValidationResult } from "../StudioApi";
import { ErrorNotice, StateNotice } from "../components/StateNotice";
import { placementIssues, type Layer } from "./geometry";
import { LayerInspector } from "./LayerInspector";
import { LayerList } from "./LayerList";
import { ScenePreview } from "./ScenePreview";
import { useCompositionSession } from "./useCompositionSession";

export default function ImageCompositionPage() {
  const { workspaceId = "", compositionId = "" } = useParams();
  const session = useCompositionSession(workspaceId, compositionId);
  const [boardId, setBoardId] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [check, setCheck] = useState<ValidationResult | null>(null);

  if (session.status === "loading") return <StateNotice title="Loading composition…" />;
  if (!session.working || !session.saved) return <ErrorNotice error={session.error} />;
  if (session.working.kind !== "image") {
    return <StateNotice tone="warning" title="Video compositions open in the timeline editor (not available in this build)" />;
  }
  const boards = session.working.artboards;
  const board = boards.find((b) => b.id === boardId) ?? boards[0];
  const layer = board.layers.find((l) => l.id === selected) ?? null;
  const issues = placementIssues(board, null);

  const replaceLayer = (next: Layer) =>
    session.edit((d) => ({
      ...d,
      artboards: d.artboards.map((b) => (b.id !== board.id ? b : { ...b, layers: b.layers.map((l) => (l.id === next.id ? next : l)) })),
    }));

  return (
    <div className="cs-page">
      <header className="cs-page-head">
        <div>
          <h2>Layout · revision {session.saved.revision}</h2>
          <p className="cs-muted">
            {saveStatusLabel(session.status, session.dirty)} · <code>{session.saved.sha256.slice(0, 12)}</code>
          </p>
        </div>
        <div className="cs-actions">
          <button type="button" onClick={session.undo} disabled={!session.canUndo}>
            Undo
          </button>
          <button type="button" onClick={session.redo} disabled={!session.canRedo}>
            Redo
          </button>
          <button
            type="button"
            onClick={() => StudioApi.validateComposition(workspaceId, session.working!).then(setCheck, () => setCheck(null))}
          >
            Check layout
          </button>
          <button type="button" className="cs-primary" onClick={() => void session.save()} disabled={!session.dirty || session.status === "saving"}>
            Save revision
          </button>
          {session.status === "conflict" ? (
            <button type="button" onClick={session.reload}>
              Reload newer version
            </button>
          ) : null}
        </div>
      </header>
      <ErrorNotice error={session.error} />
      <StateNotice title="Browser preview">
        Fonts, crops and exact pixels are resolved by the server renderer from the pinned brand files. Export uses the saved
        server render, not this preview.
      </StateNotice>
      {check ? (
        check.valid ? (
          <StateNotice title="Layout structure is valid" />
        ) : (
          <StateNotice tone="error" title="Layout issues">
            <ul>
              {check.issues.map((i, n) => (
                <li key={n}>
                  {i.code}: {i.message}
                </li>
              ))}
            </ul>
          </StateNotice>
        )
      ) : null}
      {issues.length ? (
        <StateNotice tone="warning" title="Placement">
          <ul>
            {issues.map((i) => (
              <li key={i}>{i}</li>
            ))}
          </ul>
        </StateNotice>
      ) : null}
      <nav className="cs-tabs" aria-label="Artboards">
        {boards.map((b) => (
          <button key={b.id} type="button" aria-pressed={b.id === board.id} onClick={() => setBoardId(b.id)}>
            {b.id} · {b.width}×{b.height}
          </button>
        ))}
      </nav>
      <div className="cs-editor">
        <LayerList board={board} selectedId={selected} onSelect={setSelected} />
        <div className="cs-stage" onClick={() => setSelected(null)}>
          <ScenePreview workspaceId={workspaceId} board={board} selectedId={selected} onSelect={setSelected} zone={null} />
        </div>
        {layer ? <LayerInspector board={board} layer={layer} onChange={replaceLayer} /> : <p className="cs-muted">Select a layer</p>}
      </div>
    </div>
  );
}
