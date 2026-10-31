import type { ReactNode } from "react";
import type { StudioApiError } from "../StudioApi";

type Tone = "info" | "warning" | "error";

export function StateNotice({ tone = "info", title, children }: { tone?: Tone; title: string; children?: ReactNode }) {
  return (
    <div className={`cs-notice cs-notice-${tone}`} role={tone === "error" ? "alert" : "status"}>
      <strong>{title}</strong>
      {children ? <div>{children}</div> : null}
    </div>
  );
}

const TITLES: Record<string, string> = {
  authentication_required: "Sign in required",
  capability_denied: "Your role cannot do this in this workspace",
  resource_unavailable: "Not available in this workspace",
  scope_mismatch: "That item belongs to another workspace",
  revision_conflict: "A newer version was saved",
  idempotency_conflict: "This request was already used with different content",
  network_unavailable: "Reconnecting",
  not_found: "This Studio service is not available yet",
};

export function ErrorNotice({ error }: { error: StudioApiError | null }) {
  if (!error) return null;
  const tone: Tone = error.status === 0 ? "warning" : "error";
  return (
    <StateNotice tone={tone} title={TITLES[error.code] ?? `Refused: ${error.code}`}>
      {error.message !== error.code ? error.message : null}
      {error.fieldPaths.length ? <code className="cs-paths">{error.fieldPaths.slice(0, 5).join(" · ")}</code> : null}
    </StateNotice>
  );
}

export function formatMicros(micros: number): string {
  return `$${(micros / 1_000_000).toFixed(2)}`;
}
