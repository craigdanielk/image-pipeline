export type Job = {
  id: string;
  label: string;
  product: string;
  path: string;
  dir: string;
  source: string;
};

export type Shot = {
  id: string;
  label: string;
  path: string;
  dir: string;
};

export type Product = {
  slug: string;
  name: string;
  shots: Shot[];
  geometry_description?: string;
  reference_count?: number;
  source?: string;
};

export type Output = {
  name: string;
  path: string;
  url: string;
  mtime?: number;
};

export type ArtifactStatus = "pending" | "approved" | "rejected";

export type Artifact = {
  id: string;
  path: string;
  name: string;
  url: string;
  product_slug: string;
  campaign_slug?: string | null;
  created_at: number;
  source: "job_run" | "sheet_import" | "manual";
  comment?: string;
  drive_url?: string | null;
  deleted_at?: number | null;
  delete_reason?: string | null;
  approved_at?: number | null;
  status: ArtifactStatus;
  group_key: string;
};

export type CompileFile = {
  name: string;
  path: string;
};

export type Template = {
  id: string;
  label: string;
  description: string;
  icon?: string;
  category?: string;
  defaults?: Record<string, unknown>;
};

export type CostEstimate = {
  credits?: number;
  cost?: unknown;
  raw?: string;
};

export type Campaign = {
  id: string;
  label: string;
  product_spec_id?: string;
  scene?: {
    lighting?: string;
    camera?: string;
    background?: string;
    composition?: string;
  };
};

export type Operation = {
  id: string;
  label: string;
};

export type ReferenceImage = {
  path: string;
  name: string;
  url: string;
};

export type BriefDraftResponse = {
  prompt_template: string;
  cad: string | null;
  examples_used: number;
  suggested_engine: Record<string, unknown> | null;
  suggested_output: Record<string, unknown> | null;
};

export type ExecuteResult = {
  raw_stdout: string;
  result?: Record<string, unknown>;
  outputs?: Output[];
};
