// Commerce session hook — mints + caches the commerce-scoped token for the signed-in user.
//
// Commerce is a separate service with its own short-lived RS256 token (api/commerce.ts). Rather
// than mint it on every call, we cache the session ({token, commerce_url}) in react-query keyed by
// the weespas user, with a staleTime comfortably under the commerce token TTL (120 min on the
// backend — see weespas config commerce_token_ttl_min) so we re-mint well before expiry. On any
// identity change AuthContext wipes the whole query cache, so a previous user's commerce token can
// never leak into a new session.
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import { getCommerceSession, type CommerceSession } from '../api/commerce';

// Re-mint at 60 min: half the backend's 120-min TTL, so a cached token always has generous
// headroom before commerce would reject it.
const COMMERCE_SESSION_STALE_MS = 60 * 60_000;

export interface UseCommerceSession {
  session: CommerceSession | null;
  isLoading: boolean;
  error: Error | null;
}

export function useCommerceSession(): UseCommerceSession {
  const { token, isAuthenticated, user } = useAuth();

  const { data, isLoading, error } = useQuery<CommerceSession, Error>({
    // Keyed by user id so a different sign-in gets its own entry (belt-and-braces with the
    // cache wipe in AuthContext).
    queryKey: ['commerce', 'session', user?.id ?? 'anon'],
    queryFn: () => getCommerceSession(token!),
    enabled: !!token && isAuthenticated,
    staleTime: COMMERCE_SESSION_STALE_MS,
    // A failed mint (e.g. transient weespas blip) retries once; a hard 401 is handled by
    // fetchJson (clears session → /login), so we don't loop on auth failure here.
    retry: 1,
  });

  return { session: data ?? null, isLoading, error: error ?? null };
}
