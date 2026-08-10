// Trade search hook — the commerce half of the navbar's unified search.
//
// Mirrors useTextSearch (the property half): a debounced query in, a react-query result out. The
// query is matched by the commerce backend against listing title/description/shop name and ranked
// NEAREST-FIRST nationwide, so the buyer's (lat,lng) ORDERS results but never gates them. Gated on a
// live commerce session + a ≥2-char query so it fires exactly when useTextSearch does (the two run
// concurrently and the modal merges them). Cache/stale settings match useTextSearch for a consistent
// feel across the two tabs.
import { useQuery } from '@tanstack/react-query';
import { searchTrade, type CommerceSession, type TradeSearchResult } from '../api/commerce';

const MIN_QUERY_LEN = 2;

export function useTradeSearch(
  session: CommerceSession | null,
  query: string,
  lat: number,
  lng: number,
) {
  const trimmed = query.trim();

  const result = useQuery<{ results: TradeSearchResult[]; query: string }, Error>({
    // Key on the token (session identity), the query, and the rounded location — a small move
    // shouldn't invalidate the cache, but a real relocation (or a different signed-in user's token)
    // should. 3-dp ≈ 110 m granularity, plenty for a discovery ranking.
    queryKey: ['tradeSearch', session?.token ?? 'anon', trimmed, lat.toFixed(3), lng.toFixed(3)],
    queryFn: () => searchTrade(session!, trimmed, lat, lng),
    enabled: !!session && trimmed.length >= MIN_QUERY_LEN,
    staleTime: 1000 * 60 * 2,
    gcTime: 1000 * 60 * 15,
    retry: 1,
    refetchOnWindowFocus: false,
  });

  const results = result.data?.results ?? [];

  return {
    ...result,
    results,
    total: results.length,
  };
}
