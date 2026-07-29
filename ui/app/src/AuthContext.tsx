import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
} from "react";
import type { Session, User } from "@supabase/supabase-js";
import { supabase } from "./lib/supabase";

// ---------------------------------------------------------------------------
// Public API surface
// ---------------------------------------------------------------------------
export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

export type AuthContextValue = {
  status: AuthStatus;
  session: Session | null;
  user: User | null;
  signIn: (email: string, password: string) => Promise<AuthResult>;
  signUp: (email: string, password: string) => Promise<AuthResult>;
  signOut: () => Promise<void>;
};

export type AuthResult = { ok: true } | { ok: false; error: string };

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------
const AuthContext = createContext<AuthContextValue>({
  status: "loading",
  session: null,
  user: null,
  signIn: async () => ({ ok: false, error: "AuthProvider not mounted" }),
  signUp: async () => ({ ok: false, error: "AuthProvider not mounted" }),
  signOut: async () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------
export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");

  const resolve = useCallback((s: Session | null) => {
    setSession(s);
    setStatus(s ? "authenticated" : "unauthenticated");
  }, []);

  useEffect(() => {
    // 1. Get the initial session from localStorage (persistSession: true)
    supabase.auth.getSession().then(({ data: { session: s } }) => {
      resolve(s);
    });

    // 2. Listen for auth state changes
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, s) => {
      resolve(s);
    });

    return () => subscription.unsubscribe();
  }, [resolve]);

  const signIn = useCallback(
    async (email: string, password: string): Promise<AuthResult> => {
      const { error } = await supabase.auth.signInWithPassword({
        email,
        password,
      });
      if (error) return { ok: false, error: error.message };
      return { ok: true };
    },
    []
  );

  const signUp = useCallback(
    async (email: string, password: string): Promise<AuthResult> => {
      const { error } = await supabase.auth.signUp({ email, password });
      if (error) return { ok: false, error: error.message };
      return { ok: true };
    },
    []
  );

  const signOut = useCallback(async () => {
    await supabase.auth.signOut();
    // State listener will fire and set unauthenticated
  }, []);

  return (
    <AuthContext.Provider
      value={{
        status,
        session,
        user: session?.user ?? null,
        signIn,
        signUp,
        signOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
