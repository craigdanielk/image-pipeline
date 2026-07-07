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
