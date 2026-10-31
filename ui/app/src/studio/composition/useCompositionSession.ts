// Versioned composition editing: load latest saved revision, local edits as
// whole-document snapshots (undo/redo), explicit save with If-Match and a
// stable idempotency key per pending body; conflict is surfaced, never merged.
import { useCallback, useEffect, useRef, useState } from "react";
import type { Composition } from "../contracts/types.gen";
import { newIdempotencyKey, StudioApi, StudioApiError, type CompositionFields } from "../StudioApi";
import type { SaveStatus } from "../RecipeSession";

export function editableFields(c: Composition): CompositionFields {
  return { kind: c.kind, basis_recipe: c.basis_recipe, basis_run: c.basis_run, artboards: c.artboards, video: c.video } as CompositionFields;
}

export function useCompositionSession(workspaceId: string, compositionId: string) {
  const [saved, setSaved] = useState<Composition | null>(null);
  const [working, setWorking] = useState<CompositionFields | null>(null);
  const [past, setPast] = useState<CompositionFields[]>([]);
  const [future, setFuture] = useState<CompositionFields[]>([]);
  const [status, setStatus] = useState<SaveStatus>("loading");
  const [error, setError] = useState<StudioApiError | null>(null);
  const [nonce, setNonce] = useState(0);
  const key = useRef<string>(newIdempotencyKey());

  useEffect(() => {
    const ctrl = new AbortController();
    setStatus("loading");
    setSaved(null);
    setWorking(null);
    setPast([]);
    setFuture([]);
    StudioApi.getComposition(workspaceId, compositionId, ctrl.signal)
      .then((c) => {
        setSaved(c);
        setWorking(editableFields(c));
        setStatus("saved");
        setError(null);
      })
      .catch((e: unknown) => {
        if ((e as { name?: string }).name === "AbortError") return;
        setError(e as StudioApiError);
        setStatus("load_error");
      });
    return () => ctrl.abort();
  }, [workspaceId, compositionId, nonce]);

  const edit = useCallback(
    (fn: (draft: CompositionFields) => CompositionFields) => {
      if (!working || status === "conflict" || status === "saving") return;
      setPast((p) => [...p, working]);
      setFuture([]);
      setWorking(fn(structuredClone(working)));
      key.current = newIdempotencyKey(); // body changed => new intent
      setStatus("editing");
    },
    [working, status],
  );

  const undo = useCallback(() => {
    if (!past.length || !working) return;
    setFuture((f) => [working, ...f]);
    setWorking(past[past.length - 1]);
    setPast((p) => p.slice(0, -1));
    key.current = newIdempotencyKey();
    setStatus("editing");
  }, [past, working]);

  const redo = useCallback(() => {
    if (!future.length || !working) return;
    setPast((p) => [...p, working]);
    setWorking(future[0]);
    setFuture((f) => f.slice(1));
    key.current = newIdempotencyKey();
    setStatus("editing");
  }, [future, working]);

  const save = useCallback(async () => {
    if (!saved || !working || status === "saving") return;
    setStatus("saving");
    try {
      const next = await StudioApi.reviseComposition(workspaceId, saved.id, saved.sha256, working, key.current);
      setSaved(next);
      setWorking(editableFields(next));
      setPast([]);
      setFuture([]);
      key.current = newIdempotencyKey();
      setStatus("saved");
      setError(null);
    } catch (e) {
      const err = e as StudioApiError;
      setError(err);
      setStatus(err.code === "revision_conflict" ? "conflict" : "save_error"); // retry keeps the same key
    }
  }, [saved, working, status, workspaceId]);

  return {
    saved,
    working,
    status,
    error,
    dirty: status === "editing" || status === "save_error" || status === "conflict",
    canUndo: past.length > 0,
    canRedo: future.length > 0,
    edit,
    undo,
    redo,
    save,
    reload: () => setNonce((n) => n + 1),
  };
}
