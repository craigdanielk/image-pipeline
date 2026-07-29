import { supabase } from "./lib/supabase";

// ---------------------------------------------------------------------------
// JWT helper — attaches the current Supabase session token to headers.
// Every API call goes through this so the server can verify auth.
// ---------------------------------------------------------------------------
export async function authHeaders(): Promise<Record<string, string>> {
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (session?.access_token) {
    headers["Authorization"] = `Bearer ${session.access_token}`;
  }
  return headers;
}

// ---------------------------------------------------------------------------
// Generic JSON API call — carries the JWT.
// ---------------------------------------------------------------------------
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = await authHeaders();
  const res = await fetch(path, {
    headers: { ...headers, ...(options.headers as Record<string, string> || {}) },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail =
      typeof (data as { detail?: unknown }).detail === "string"
        ? (data as { detail: string }).detail
        : JSON.stringify((data as { detail?: unknown }).detail || data);
    throw new Error(detail || res.statusText);
  }
  return data as T;
}

// ---------------------------------------------------------------------------
// File upload — carries the JWT (FormData, not JSON).
// ---------------------------------------------------------------------------
export async function uploadFile(
  productSlug: string,
  file: File
): Promise<{ path: string; url: string; filename: string }> {
  const headers = await authHeaders();
  // Do NOT set Content-Type for FormData — the browser sets it with boundary.
  delete headers["Content-Type"];

  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`/api/upload?product_slug=${encodeURIComponent(productSlug)}`, {
    method: "POST",
    headers,
    body: form,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((data as { detail?: string }).detail || res.statusText);
  }
  return data as { path: string; url: string; filename: string };
}

// ---------------------------------------------------------------------------
// Multi-file extract upload — carries the JWT (FormData, not JSON).
// ---------------------------------------------------------------------------
export async function extractProductFromPhotos(
  photos: File[],
  name: string,
  notes: string
): Promise<{ draft: Record<string, unknown> }> {
  const headers = await authHeaders();
  delete headers["Content-Type"];

  const form = new FormData();
  form.append("name", name);
  form.append("notes", notes);
  photos.forEach((f) => form.append("photos", f));
  const res = await fetch("/api/knowledge/products/extract", { method: "POST", headers, body: form });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((data as { detail?: string }).detail || res.statusText);
  }
  return data as { draft: Record<string, unknown> };
}
