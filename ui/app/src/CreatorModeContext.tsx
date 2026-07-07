import { createContext, useContext, useState, type ReactNode } from "react";

type CreatorModeContextValue = {
  creatorMode: boolean;
  setCreatorMode: (v: boolean) => void;
  toggleMode: () => void;
};

const CreatorModeContext = createContext<CreatorModeContextValue | null>(null);

export function CreatorModeProvider({ children }: { children: ReactNode }) {
  const [creatorMode, setCreatorMode] = useState(true);
  const toggleMode = () => setCreatorMode((m) => !m);
  return (
    <CreatorModeContext.Provider value={{ creatorMode, setCreatorMode, toggleMode }}>
      {children}
    </CreatorModeContext.Provider>
  );
}

export function useCreatorMode() {
  const ctx = useContext(CreatorModeContext);
  if (!ctx) throw new Error("useCreatorMode must be used within CreatorModeProvider");
  return ctx;
}
