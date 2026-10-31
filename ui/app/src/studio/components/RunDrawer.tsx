// Job details drawer: server-owned run state, nodes, ordered events and cost
// state. "Unknown" cost is shown as unknown, never as zero.
import { useSavedJob, type Connection } from "../useSavedJob";
import { ErrorNotice } from "./StateNotice";

const CONNECTION: Record<Connection, string> = {
  connecting: "Connecting…",
  live: "Live",
  reconnecting: "Reconnecting — the job continues on the server",
  closed: "Finished",
  refused: "Not available",
};

export function RunDrawer({ workspaceId, runId, onClose }: { workspaceId: string; runId: string; onClose: () => void }) {
  const job = useSavedJob(workspaceId, runId);
  return (
    <aside className="cs-drawer" aria-label="Job details">
      <header>
        <h3>Job {runId.slice(0, 8)}</h3>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </header>
      <p className="cs-muted">{CONNECTION[job.connection]}</p>
      {job.connection === "refused" ? <ErrorNotice error={job.error} /> : null}
      {job.run ? (
        <dl className="cs-facts">
          <dt>State</dt>
          <dd>{job.run.state}</dd>
          <dt>Cost</dt>
          <dd>{job.run.cost_state === "unknown" ? "Unknown (pending provider confirmation)" : job.run.cost_state ?? "—"}</dd>
          <dt>Failure</dt>
          <dd>{job.run.failure_code ?? "—"}</dd>
          <dt>Events</dt>
          <dd>{job.run.event_sequence}</dd>
        </dl>
      ) : null}
      <h4>Steps</h4>
      <ul className="cs-list">
        {job.nodes.map((n) => (
          <li key={n.node_id}>
            <code>{n.node_id}</code> — {n.state} (attempt {n.attempt_number}, {n.output_refs.length} outputs)
          </li>
        ))}
      </ul>
      <h4>Events</h4>
      <ol className="cs-list">
        {job.events.map((e) => (
          <li key={e.sequence}>
            #{e.sequence} {e.kind} <span className="cs-muted">{new Date(e.occurred_at).toLocaleString()}</span>
          </li>
        ))}
      </ol>
    </aside>
  );
}
