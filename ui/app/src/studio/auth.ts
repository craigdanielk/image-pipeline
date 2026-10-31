// Bearer-token seam for the hosted Studio API. The token comes from the
// deployment's sign-in (Supabase Auth session, wired in CS-B5). Nothing here
// stores credentials, tenant slugs or disk roots; without a token every Studio
// call is refused and the UI says so.
export type AccessTokenProvider = () => Promise<string | null>;

let provider: AccessTokenProvider = async () => null;

export function setAccessTokenProvider(next: AccessTokenProvider): void {
  provider = next;
}

export function accessToken(): Promise<string | null> {
  return provider();
}
