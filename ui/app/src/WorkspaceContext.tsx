import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";

type WorkspaceStats = {
  products: number;
  campaigns: number;
  runs: number;
  runs_active: number;
};

type WorkspaceInfo = {
  id: string;
  name: string;
};

type WorkspaceContextValue = {
  workspace: WorkspaceInfo | null;
  stats: WorkspaceStats | null;
  refresh: () => void;
};

const WorkspaceContext = createContext<WorkspaceContextValue>({
  workspace: null,
  stats: null,
  refresh: () => {},
});

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [workspace, setWorkspace] = useState<WorkspaceInfo | null>(null);
  const [stats, setStats] = useState<WorkspaceStats | null>(null);

  const refresh = () => {
    api<{ workspace: WorkspaceInfo; stats: WorkspaceStats }>("/api/workspace")
      .then((d) => {
        setWorkspace(d.workspace);
        setStats(d.stats);
      })
      .catch(() => {});
  };

  useEffect(() => {
    refresh();
  }, []);

  return (
    <WorkspaceContext.Provider value={{ workspace, stats, refresh }}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace() {
  return useContext(WorkspaceContext);
}
