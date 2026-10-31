// One immutable export: file, lineage, exact copy, rights and cost, download,
// and "Send to MOS". Sending binds this exact export; it is not approval.
import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import type { MosHandoff, VersionRef } from "../contracts/types.gen";
import { exportRef, newIdempotencyKey, StudioApi, StudioApiError, type StoredExport } from "../StudioApi";
import { ErrorNotice, formatMicros, StateNotice } from "../components/StateNotice";

/** Provided by a MOS-origin request (router state); Studio never invents MOS targets. */
export type MosHandoffContext = { target: MosHandoff["target"]; creativeRequest: VersionRef };

export default function ExportsPage() {
  const { workspaceId = "", versionId = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const mos = (location.state as { mos?: MosHandoffContext } | null)?.mos ?? null;
  const [manifest, setManifest] = useState<StoredExport | null>(null);
  const [error, setError] = useState<StudioApiError | null>(null);
  const [sending, setSending] = useState(false);
  const [sendKey] = useState(newIdempotencyKey); // one intent per page view; a retry reuses it

  useEffect(() => {
    const ctrl = new AbortController();
    setManifest(null);
    StudioApi.getExport(workspaceId, versionId, ctrl.signal)
      .then(setManifest)
      .catch((e: unknown) => (e as { name?: string }).name !== "AbortError" && setError(e as StudioApiError));
    return () => ctrl.abort();
  }, [workspaceId, versionId]);

  if (!manifest) return error ? <ErrorNotice error={error} /> : <StateNotice title="Loading export…" />;
  const ref = manifest.file.ref;
  const cost = manifest.cost;

  const download = async () => {
    try {
      const ticket = await StudioApi.downloadTicket(workspaceId, ref.asset_id, ref.asset_version_id, ref.sha256);
      window.open(ticket.url, "_blank", "noopener");
    } catch (e) {
      setError(e as StudioApiError);
    }
  };

  const send = async () => {
    if (!mos) return;
    setSending(true);
    try {
      const view = await StudioApi.sendToMos(
        workspaceId,
        { exports: [exportRef(manifest)], target: mos.target, creative_request: mos.creativeRequest },
        sendKey,
      );
      navigate(`/studio/${workspaceId}/handoffs/${view.handoff.id}`);
    } catch (e) {
      setError(e as StudioApiError);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="cs-page">
      <header className="cs-page-head">
        <div>
          <h2>Export {manifest.format.preset}</h2>
          <p className="cs-muted">
            {manifest.format.width_px}×{manifest.format.height_px} {manifest.format.mime_type} · <code>{ref.sha256.slice(0, 16)}</code>
          </p>
        </div>
        <div className="cs-actions">
          <button type="button" onClick={() => void download()}>
            Download
          </button>
          <button type="button" className="cs-primary" disabled={!mos || sending} onClick={() => void send()}>
            {sending ? "Sending…" : "Send to MOS"}
          </button>
        </div>
      </header>
      <ErrorNotice error={error} />
      {!mos ? (
        <StateNotice tone="warning" title="Send to MOS needs a MOS-origin request">
          Open this export from the MOS campaign that requested it, so the exact concept, copy, destination and policy
          versions are bound. Studio does not guess them.
        </StateNotice>
      ) : null}
      <StateNotice title="Not an approval">
        A Studio export is exact bytes plus provenance. MOS review decides publication.
      </StateNotice>
      <dl className="cs-facts">
        <dt>Renderer</dt>
        <dd>
          <code>{manifest.lineage.render_engine_version}</code>
        </dd>
        <dt>Composition</dt>
        <dd>
          rev {manifest.lineage.composition.revision} · <code>{manifest.lineage.composition.sha256.slice(0, 12)}</code>
        </dd>
        <dt>Brand package</dt>
        <dd>
          rev {manifest.lineage.brand_package.revision} · <code>{manifest.lineage.brand_package.sha256.slice(0, 12)}</code>
        </dd>
        <dt>Fonts</dt>
        <dd>{manifest.lineage.font_asset_sha256s.map((s) => s.slice(0, 12)).join(", ")}</dd>
        <dt>Generated sources</dt>
        <dd>{manifest.lineage.generated_sources.length} attempt(s)</dd>
        <dt>Rights</dt>
        <dd>
          {manifest.rights_summary.status}
          {manifest.rights_summary.attribution_text ? ` · ${manifest.rights_summary.attribution_text}` : ""}
        </dd>
        <dt>Cost</dt>
        <dd>
          {cost.state === "unknown"
            ? `Unknown — up to ${formatMicros(cost.unresolved_micros)} unresolved`
            : `${cost.state}: ${formatMicros(cost.settled_micros)} settled`}
        </dd>
      </dl>
      <h3>Exact copy</h3>
      <ul className="cs-list">
        {manifest.exact_copy.map((c) => (
          <li key={c.copy_id}>
            <strong>{c.copy_id}</strong>: “{c.text}” <code>{c.text_sha256.slice(0, 12)}</code>{" "}
            {c.source_copy ? <span className="cs-muted">MOS copy rev {c.source_copy.revision}</span> : <em>no MOS copy version</em>}
          </li>
        ))}
      </ul>
    </div>
  );
}
