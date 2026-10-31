// Contract client for the hosted Creative Studio API (/api/v1). Every call is
// authenticated, workspace-scoped and returns typed refusals. Mutations carry
// an Idempotency-Key; revisions carry If-Match. No provider URL, credential,
// local path or legacy execute route is used from here.
import { accessToken } from "./auth";
import type {
  AssetRef,
  Composition,
  Error as ContractError,
  ExportManifest,
  MosHandoff,
  MosIngestionReceipt,
  Recipe,
  RecipeCommandBatch,
  VersionRef,
} from "./contracts/types.gen";

export class StudioApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryable = false,
    readonly fieldPaths: string[] = [],
  ) {
    super(message);
  }
}

export type WorkspaceSummary = { id: string; name: string; account_id: string; mode: string; role: string | null };
export type RunRow = {
  id: string;
  state: string;
  event_sequence: number;
  cost_state: string | null;
  failure_code: string | null;
  created_at: string;
  updated_at: string;
};
export type RunNode = { node_id: string; state: string; attempt_number: number; output_refs: AssetRef[] };
export type RunEventRow = { sequence: number; kind: string; payload: Record<string, unknown>; occurred_at: string };
export type RunSnapshot = { run: RunRow & { workspace_id: string }; nodes: RunNode[]; events: RunEventRow[] };
export type AssetRow = {
  id: string;
  label: string;
  kind: string;
  status: string;
  latest_version_id: string | null;
  revision: number | null;
  sha256: string | null;
  mime_type: string | null;
  size_bytes: number | null;
  width: number | null;
  height: number | null;
  duration_ms: number | null;
};
export type DownloadTicket = { url: string; expires_in: number; sha256: string; mime_type: string; size_bytes: number };
export type CompositionFields = Pick<Composition, "kind" | "basis_recipe" | "basis_run" | "artboards" | "video">;
export type HandoffState = "queued" | "delivering" | "received" | "needs_reconciliation" | "failed";
export type HandoffView = { handoff: MosHandoff; state: HandoffState; receipt: MosIngestionReceipt | null };
export type ValidationResult = { valid: boolean; issues: ContractError[]; semantic_sha256?: string };

const BASE = "/api/v1";

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

function refusal(status: number, body: unknown): StudioApiError {
  const b = (body ?? {}) as Record<string, unknown>;
  const err = b.error;
  if (err && typeof err === "object") {
    const e = err as ContractError;
    return new StudioApiError(status, e.code, e.message, e.retryable, e.field_paths);
  }
  const code = typeof err === "string" ? err : `http_${status}`;
  const detail = typeof b.detail === "string" ? b.detail : code;
  return new StudioApiError(status, code, detail, status >= 500 || status === 0);
}

type CallOptions = {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  idempotencyKey?: string;
  ifMatch?: string;
  signal?: AbortSignal;
};

async function call<T>(path: string, opts: CallOptions = {}): Promise<T> {
  const token = await accessToken();
  if (!token) throw new StudioApiError(401, "authentication_required", "Sign in to use Creative Studio.");
  const headers: Record<string, string> = { Authorization: `Bearer ${token}`, Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;
  if (opts.ifMatch) headers["If-Match"] = `"${opts.ifMatch}"`;
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: opts.signal,
      cache: "no-store",
    });
  } catch (e) {
    if ((e as { name?: string }).name === "AbortError") throw e;
    throw new StudioApiError(0, "network_unavailable", "Connection lost. Saved work is unaffected.", true);
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw refusal(res.status, body);
  return body as T;
}

const ws = (workspaceId: string) => `/workspaces/${encodeURIComponent(workspaceId)}`;

export const StudioApi = {
  // -- workspaces / jobs / library (CS-B1) --
  myWorkspaces: (signal?: AbortSignal) =>
    call<{ workspaces: WorkspaceSummary[] }>("/me/workspaces", { signal }).then((r) => r.workspaces),
  listRuns: (workspaceId: string, signal?: AbortSignal) =>
    call<{ runs: RunRow[] }>(`${ws(workspaceId)}/runs?limit=50`, { signal }).then((r) => r.runs),
  getRun: (workspaceId: string, runId: string, after: number, signal?: AbortSignal) =>
    call<RunSnapshot>(`${ws(workspaceId)}/runs/${runId}?after=${after}`, { signal }),
  listAssets: (workspaceId: string, signal?: AbortSignal) =>
    call<{ assets: AssetRow[] }>(`${ws(workspaceId)}/assets?limit=100`, { signal }).then((r) => r.assets),
  downloadTicket: (workspaceId: string, assetId: string, versionId: string, sha256: string) =>
    call<DownloadTicket>(`${ws(workspaceId)}/assets/${assetId}/versions/${versionId}/download`, {
      method: "POST",
      body: { sha256 },
    }),

  // -- recipes (service surface owned by CS-B2/B3; refused until deployed) --
  getRecipe: (workspaceId: string, recipeId: string, versionId: string, signal?: AbortSignal) =>
    call<{ data: Recipe }>(`${ws(workspaceId)}/recipes/${recipeId}/versions/${versionId}`, { signal }).then(
      (r) => r.data,
    ),
  reviseRecipe: (workspaceId: string, batch: RecipeCommandBatch, idempotencyKey: string) =>
    call<{ data: Recipe }>(`${ws(workspaceId)}/recipes/${batch.base_recipe.id}/revisions`, {
      method: "POST",
      body: batch,
      idempotencyKey,
      ifMatch: batch.base_recipe.sha256,
    }).then((r) => r.data),

  // -- compositions / exports / handoffs (CS-B4) --
  getComposition: (workspaceId: string, compositionId: string, signal?: AbortSignal) =>
    call<{ data: Composition }>(`${ws(workspaceId)}/compositions/${compositionId}`, { signal }).then((r) => r.data),
  createComposition: (workspaceId: string, fields: CompositionFields, idempotencyKey: string) =>
    call<{ data: Composition }>(`${ws(workspaceId)}/compositions`, { method: "POST", body: fields, idempotencyKey }).then(
      (r) => r.data,
    ),
  reviseComposition: (
    workspaceId: string,
    compositionId: string,
    expectedSha256: string,
    fields: CompositionFields,
    idempotencyKey: string,
  ) =>
    call<{ data: Composition }>(`${ws(workspaceId)}/compositions/${compositionId}/revisions`, {
      method: "POST",
      body: fields,
      idempotencyKey,
      ifMatch: expectedSha256,
    }).then((r) => r.data),
  validateComposition: (workspaceId: string, fields: CompositionFields) =>
    call<{ data: ValidationResult }>(`${ws(workspaceId)}/compositions/validate`, { method: "POST", body: fields }).then(
      (r) => r.data,
    ),
  getExport: (workspaceId: string, versionId: string, signal?: AbortSignal) =>
    call<{ data: ExportManifest }>(`${ws(workspaceId)}/exports/${versionId}`, { signal }).then((r) => r.data),
  sendToMos: (
    workspaceId: string,
    body: { exports: VersionRef[]; target: MosHandoff["target"]; creative_request: VersionRef },
    idempotencyKey: string,
  ) =>
    call<{ data: HandoffView }>(`${ws(workspaceId)}/handoffs/marketing-os`, {
      method: "POST",
      body,
      idempotencyKey,
    }).then((r) => r.data),
  getHandoff: (workspaceId: string, handoffId: string, signal?: AbortSignal) =>
    call<{ data: HandoffView }>(`${ws(workspaceId)}/handoffs/marketing-os/${handoffId}`, { signal }).then(
      (r) => r.data,
    ),
};

export function exportRef(manifest: ExportManifest): VersionRef {
  return {
    authority: "creative-studio",
    workspace_id: manifest.workspace_id,
    kind: "export",
    id: manifest.id,
    version_id: manifest.version_id,
    revision: manifest.revision,
    sha256: manifest.sha256,
  };
}
