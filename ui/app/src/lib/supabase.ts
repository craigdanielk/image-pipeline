import { createClient } from "@supabase/supabase-js";

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;

function missingEnv(name: string): never {
  throw new Error(
    `Missing Vite env var: ${name}. ` +
      "Set it in ui/app/.env or pass it when running Vite (e.g. VITE_SUPABASE_URL=... VITE_SUPABASE_ANON_KEY=... npm run dev)."
  );
}

if (!SUPABASE_URL) missingEnv("VITE_SUPABASE_URL");
if (!SUPABASE_ANON_KEY) missingEnv("VITE_SUPABASE_ANON_KEY");

export const supabase = createClient(SUPABASE_URL!, SUPABASE_ANON_KEY!, {
  auth: {
    autoRefreshToken: true,
    persistSession: true,
    storage: typeof window !== "undefined" ? window.localStorage : undefined,
  },
});
