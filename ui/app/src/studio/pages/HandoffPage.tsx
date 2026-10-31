// Delivery status of one MOS handoff. A receipt means MOS stored the exact
// bytes; it always starts pending review and is never publication approval.
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { StudioApi, StudioApiError, type HandoffView } from "../StudioApi";
import { ErrorNotice, StateNotice } from "../components/StateNotice";

const LABEL: Record<HandoffView["state"], string> = {
  queued: "Queued for delivery",
  delivering: "Delivering to MOS",
  received: "Received by MOS · pending MOS review",
  needs_reconciliation: "Checking delivery with MOS",
  failed: "Delivery failed · export ready, not sent",
};

export default function HandoffPage() {
  const { workspaceId = "", handoffId = "" } = useParams();
  const [view, setView] = useState<HandoffView | null>(null);
  const [error, setError] = useState<StudioApiError | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    StudioApi.getHandoff(workspaceId, handoffId, ctrl.signal)
      .then(setView)
      .catch((e: unknown) => (e as { name?: string }).name !== "AbortError" && setError(e as StudioApiError));
    return () => ctrl.abort();
  }, [workspaceId, handoffId]);

  if (!view) return error ? <ErrorNotice error={error} /> : <StateNotice title="Loading delivery…" />;
  return (
    <div className="cs-page">
      <h2>Send to MOS</h2>
      <StateNotice tone={view.state === "failed" ? "error" : "info"} title={LABEL[view.state]} />
      <ul className="cs-list">
        {view.handoff.exports.map((e) => (
          <li key={e.version_id}>
            Export rev {e.revision} · <code>{e.sha256.slice(0, 16)}</code>
          </li>
        ))}
      </ul>
      {view.receipt ? (
        <StateNotice title={`MOS receipt · review ${view.receipt.review_status}`}>
          {view.receipt.items.length} item(s) stored with matching hashes; publication eligible: no.
        </StateNotice>
      ) : null}
    </div>
  );
}
