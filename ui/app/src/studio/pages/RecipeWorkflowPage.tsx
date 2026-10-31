// Builder sections and step list projected from the same RecipeSession. The
// recipe service (save/load) is owned by CS-B2/B3; until it is deployed the
// session shows the API's typed refusal instead of local fake state.
import { useParams } from "react-router-dom";
import { RecipeSessionProvider, saveStatusLabel, useRecipeSession } from "../RecipeSession";
import { recipeCommands } from "../recipeCommands";
import { builderSections, workflowGraph } from "../recipeProjection";
import { ErrorNotice, StateNotice } from "../components/StateNotice";

function Workflow() {
  const s = useRecipeSession();
  if (s.status === "loading") return <StateNotice title="Loading recipe…" />;
  if (!s.working) return <ErrorNotice error={s.error} />;
  const sections = builderSections(s.working);
  const graph = workflowGraph(s.working);
  return (
    <div className="cs-page">
      <header className="cs-page-head">
        <div>
          <h2>Recipe · revision {s.saved?.revision}</h2>
          <p className="cs-muted">{saveStatusLabel(s.status, s.dirty)}</p>
        </div>
        <div className="cs-actions">
          <button type="button" onClick={s.undo} disabled={!s.canUndo}>
            Undo
          </button>
          <button type="button" onClick={s.redo} disabled={!s.canRedo}>
            Redo
          </button>
          <button
            type="button"
            onClick={() =>
              s.dispatch([
                recipeCommands.setAuthoringView({
                  ...s.working!.authoring_view,
                  last_view: s.working!.authoring_view.last_view === "guided" ? "canvas" : "guided",
                }),
              ])
            }
          >
            Switch view
          </button>
          <button type="button" className="cs-primary" disabled={!s.dirty || s.status === "saving"} onClick={() => void s.save()}>
            Save
          </button>
          {s.status === "conflict" ? (
            <button type="button" onClick={s.reload}>
              Reload newer version
            </button>
          ) : null}
        </div>
      </header>
      <ErrorNotice error={s.error} />
      {s.working.authoring_view.last_view === "guided" ? (
        sections.map((sec) => (
          <section key={sec.id} className="cs-section">
            <h3>{sec.title}</h3>
            <ul className="cs-list">
              {sec.nodeIds.map((id) => (
                <li key={id}>
                  <code>{id}</code>
                </li>
              ))}
            </ul>
            {sec.issues.map((i) => (
              <StateNotice key={i} tone="warning" title={i} />
            ))}
          </section>
        ))
      ) : (
        <section className="cs-section" aria-label="Workflow steps (accessible list)">
          <h3>Workflow</h3>
          <ol className="cs-list">
            {graph.nodes.map((n) => (
              <li key={n.id}>
                <code>{n.id}</code> · {n.kind}
                <ul>
                  {graph.edges
                    .filter((e) => e.target === n.id)
                    .map((e) => (
                      <li key={e.id}>
                        {e.targetPort}[{e.ordinal}] ← {e.source}.{e.sourcePort}
                      </li>
                    ))}
                </ul>
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}

export default function RecipeWorkflowPage() {
  const { workspaceId = "", recipeId = "", versionId = "" } = useParams();
  return (
    <RecipeSessionProvider workspaceId={workspaceId} recipeId={recipeId} versionId={versionId}>
      <Workflow />
    </RecipeSessionProvider>
  );
}
