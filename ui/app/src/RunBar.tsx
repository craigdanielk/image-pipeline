import type { CostEstimate } from "./types";

type Props = {
  loading: boolean;
  hasJob: boolean;
  hasCompile: boolean;
  status: string;
  statusError: boolean;
  costEstimate: CostEstimate | null;
  onCompile: () => void;
  onEstimate: () => void;
  onGenerate: () => void;
};

export default function RunBar({
  loading,
  hasJob,
  hasCompile,
  status,
  statusError,
  costEstimate,
  onCompile,
  onEstimate,
  onGenerate,
}: Props) {
  const credits =
    costEstimate?.credits ??
    (typeof costEstimate?.cost === "object" &&
    costEstimate?.cost !== null &&
    "credits" in (costEstimate.cost as object)
      ? (costEstimate.cost as { credits?: number }).credits
      : undefined);

  return (
    <footer className="run-bar">
      <div className="run-bar__actions">
        <button
          type="button"
          className="btn btn--primary"
          onClick={onCompile}
          disabled={!hasJob || loading}
        >
          Compile
        </button>
        <button
          type="button"
          className="btn"
          onClick={onEstimate}
          disabled={!hasCompile || loading}
        >
          Estimate cost
        </button>
        <button
          type="button"
          className="btn btn--generate"
          onClick={onGenerate}
          disabled={!hasCompile || loading}
        >
          Generate
          {credits != null && (
            <span className="credit-badge">{credits} cr</span>
          )}
        </button>
      </div>
      <p className={`run-bar__status ${statusError ? "error" : ""} ${loading ? "loading" : ""}`}>
        {status || "Select a shot and compile to begin"}
      </p>
    </footer>
  );
}
