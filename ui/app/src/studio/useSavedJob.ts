// Durable job view: initial snapshot, then cursor-based event reads resumable
// by sequence. Connection loss is "reconnecting", never a job failure; only the
// server's run state is shown as the job's state. Unsubscribes on unmount or
// scope change (no stale workspace data).
import { useEffect, useRef, useState } from "react";
import { StudioApi, StudioApiError, type RunEventRow, type RunNode, type RunRow } from "./StudioApi";

export type Connection = "connecting" | "live" | "reconnecting" | "closed" | "refused";

export type SavedJob = {
  run: RunRow | null;
  nodes: RunNode[];
  events: RunEventRow[];
  connection: Connection;
  error: StudioApiError | null;
};

const TERMINAL = new Set(["completed", "failed", "cancelled"]);
// Presentation refresh cadence (not a business limit): base poll and reconnect backoff cap.
const POLL_MS = 2000;
const MAX_BACKOFF_MS = 30000;

export function useSavedJob(workspaceId: string | null, runId: string | null): SavedJob {
  const [state, setState] = useState<SavedJob>({ run: null, nodes: [], events: [], connection: "connecting", error: null });
  const cursor = useRef(0);

  useEffect(() => {
    if (!workspaceId || !runId) return;
    const ctrl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let backoff = POLL_MS;
    cursor.current = 0;
    setState({ run: null, nodes: [], events: [], connection: "connecting", error: null });

    const tick = async () => {
      try {
        const snap = await StudioApi.getRun(workspaceId, runId, cursor.current, ctrl.signal);
        const fresh = snap.events.filter((e) => e.sequence > cursor.current);
        if (fresh.length) cursor.current = fresh[fresh.length - 1].sequence;
        backoff = POLL_MS;
        const done = TERMINAL.has(snap.run.state) && cursor.current >= snap.run.event_sequence;
        setState((s) => ({
          run: snap.run,
          nodes: snap.nodes,
          events: [...s.events, ...fresh],
          connection: done ? "closed" : "live",
          error: null,
        }));
        if (!done) timer = setTimeout(tick, fresh.length ? 0 : POLL_MS);
      } catch (e) {
        if ((e as { name?: string }).name === "AbortError") return;
        const err = e as StudioApiError;
        if (err.status === 401 || err.status === 403 || err.status === 404) {
          setState((s) => ({ ...s, connection: "refused", error: err }));
          return;
        }
        setState((s) => ({ ...s, connection: "reconnecting", error: err }));
        backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
        timer = setTimeout(tick, backoff);
      }
    };
    void tick();
    return () => {
      ctrl.abort();
      if (timer) clearTimeout(timer);
    };
  }, [workspaceId, runId]);

  return state;
}
