// One canonical recipe session shared by Builder and Workflow views:
// optimistic working copy, coalesced save queue with If-Match + idempotency key,
// undo/redo as new edits, explicit conflict/error states. Never last-writer-wins.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Recipe } from "./contracts/types.gen";
import { applyCommands, recipeRef, type RecipeCommand } from "./recipeCommands";
import { newIdempotencyKey, StudioApi, StudioApiError } from "./StudioApi";

export type SaveStatus = "loading" | "load_error" | "saved" | "editing" | "saving" | "save_error" | "conflict";

type Pending = { commands: RecipeCommand[]; idempotencyKey: string };

export type RecipeSessionValue = {
  saved: Recipe | null;
  working: Recipe | null;
  status: SaveStatus;
  error: StudioApiError | null;
  dirty: boolean;
  canUndo: boolean;
  canRedo: boolean;
  dispatch: (commands: RecipeCommand[]) => void;
  undo: () => void;
  redo: () => void;
  save: () => Promise<void>;
  reload: () => void;
};

const Ctx = createContext<RecipeSessionValue | null>(null);

export function useRecipeSession(): RecipeSessionValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useRecipeSession outside RecipeSessionProvider");
  return v;
}

type Props = { workspaceId: string; recipeId: string; versionId: string; children: ReactNode };

export function RecipeSessionProvider({ workspaceId, recipeId, versionId, children }: Props) {
  const [saved, setSaved] = useState<Recipe | null>(null);
  const [status, setStatus] = useState<SaveStatus>("loading");
  const [error, setError] = useState<StudioApiError | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [undoStack, setUndo] = useState<RecipeCommand[][]>([]);
  const [redoStack, setRedo] = useState<RecipeCommand[][]>([]);
  const [loadNonce, setLoadNonce] = useState(0);
  const inFlight = useRef(false);

  useEffect(() => {
    const ctrl = new AbortController();
    setStatus("loading");
    setSaved(null);
    setPending(null);
    setUndo([]);
    setRedo([]);
    StudioApi.getRecipe(workspaceId, recipeId, versionId, ctrl.signal)
      .then((r) => {
        setSaved(r);
        setStatus("saved");
        setError(null);
      })
      .catch((e: unknown) => {
        if ((e as { name?: string }).name === "AbortError") return;
        setError(e as StudioApiError);
        setStatus("load_error");
      });
    return () => ctrl.abort();
  }, [workspaceId, recipeId, versionId, loadNonce]);

  const working = useMemo(() => {
    if (!saved) return null;
    return pending ? applyCommands(saved, pending.commands).next : saved;
  }, [saved, pending]);

  const dispatch = useCallback(
    (commands: RecipeCommand[]) => {
      if (!saved || !commands.length || status === "conflict") return;
      const base = pending ? applyCommands(saved, pending.commands).next : saved;
      const { inverse } = applyCommands(base, commands); // throws RecipeCommandError on invalid edits
      setUndo((u) => [...u, inverse]);
      setRedo([]);
      // a new edit after a failed save needs a new key: the request body changed
      setPending((p) => ({ commands: [...(p?.commands ?? []), ...commands], idempotencyKey: newIdempotencyKey() }));
      setStatus("editing");
    },
    [saved, pending, status],
  );

  const undo = useCallback(() => {
    const last = undoStack[undoStack.length - 1];
    if (!last || !saved) return;
    const base = pending ? applyCommands(saved, pending.commands).next : saved;
    const { inverse } = applyCommands(base, last);
    setUndo((u) => u.slice(0, -1));
    setRedo((r) => [...r, inverse]);
    setPending((p) => ({ commands: [...(p?.commands ?? []), ...last], idempotencyKey: newIdempotencyKey() }));
    setStatus("editing");
  }, [undoStack, saved, pending]);

  const redo = useCallback(() => {
    const last = redoStack[redoStack.length - 1];
    if (!last || !saved) return;
    const base = pending ? applyCommands(saved, pending.commands).next : saved;
    const { inverse } = applyCommands(base, last);
    setRedo((r) => r.slice(0, -1));
    setUndo((u) => [...u, inverse]);
    setPending((p) => ({ commands: [...(p?.commands ?? []), ...last], idempotencyKey: newIdempotencyKey() }));
    setStatus("editing");
  }, [redoStack, saved, pending]);

  const save = useCallback(async () => {
    if (!saved || !pending || inFlight.current) return;
    inFlight.current = true;
    setStatus("saving");
    const sent = pending;
    try {
      const next = await StudioApi.reviseRecipe(
        workspaceId,
        { schema_version: "cs-recipe-command-v1", base_recipe: recipeRef(saved), commands: sent.commands },
        sent.idempotencyKey, // a retry after a lost response reuses the same key
      );
      setSaved(next);
      setPending((p) => (p === sent ? null : p && { ...p, commands: p.commands.slice(sent.commands.length) }));
      setStatus("saved");
      setError(null);
    } catch (e) {
      const err = e as StudioApiError;
      setError(err);
      setStatus(err.code === "revision_conflict" ? "conflict" : "save_error");
    } finally {
      inFlight.current = false;
    }
  }, [saved, pending, workspaceId]);

  const value: RecipeSessionValue = {
    saved,
    working,
    status,
    error,
    dirty: !!pending,
    canUndo: undoStack.length > 0,
    canRedo: redoStack.length > 0,
    dispatch,
    undo,
    redo,
    save,
    reload: () => setLoadNonce((n) => n + 1),
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function saveStatusLabel(status: SaveStatus, dirty: boolean): string {
  switch (status) {
    case "loading":
      return "Loading…";
    case "load_error":
      return "Could not load this version";
    case "saving":
      return "Saving…";
    case "save_error":
      return "Changes not saved";
    case "conflict":
      return "Someone saved a newer version — reload or keep your changes as a new draft";
    default:
      return dirty ? "Unsaved changes" : "All changes saved";
  }
}
