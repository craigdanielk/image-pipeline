export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
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

export async function uploadFile(
  productSlug: string,
  file: File
): Promise<{ path: string; url: string; filename: string }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`/api/upload?product_slug=${encodeURIComponent(productSlug)}`, {
    method: "POST",
    body: form,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((data as { detail?: string }).detail || res.statusText);
  }
  return data as { path: string; url: string; filename: string };
}

export async function extractProductFromPhotos(
  photos: File[],
  name: string,
  notes: string
): Promise<{ draft: Record<string, unknown> }> {
  const form = new FormData();
  form.append("name", name);
  form.append("notes", notes);
  photos.forEach((f) => form.append("photos", f));
  const res = await fetch("/api/knowledge/products/extract", { method: "POST", body: form });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((data as { detail?: string }).detail || res.statusText);
  }
  return data as { draft: Record<string, unknown> };
}
