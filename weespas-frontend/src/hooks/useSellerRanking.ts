// useSellerRanking — the Ranking Card's data source (§8, Chunk B).
//
// The server-side cache is 5 minutes; we align the client's staleTime + refetchInterval to the
// same window so a well-behaved client NEVER hammers the endpoint. `radiusKm` participates in the
// query key: sliding the radius picker is a fresh fetch (a different peer set entirely), not a
// stale-render of the previous ring.
//
// The response is a discriminated union (RankingOut / RankingPaywallOut / RankingUnavailableOut);
// this hook is pass-through — the caller (RankingCard) branches on `data.kind`. Bundling three
// hooks (one per kind) would just duplicate the fetch machinery.
import { useQuery } from '@tanstack/react-query';
import { getMyRanking, type CommerceSession, type RankingResponse } from '../api/commerce';

/** 5 minutes — matches the server's cache TTL exactly. Any faster refetch just hits the same
 *  cached payload, so we save the round-trip. */
export const RANKING_REFRESH_MS = 5 * 60_000;

export const RANKING_QUERY_KEY = ['commerce', 'ranking', 'me'] as const;

export function useSellerRanking(
  session: CommerceSession | null,
  radiusKm: number,
) {
  return useQuery<RankingResponse, Error>({
    // Keyed by (commerce base, radius). A user with two commerce sessions (dev vs prod origin) or
    // two open sliders on different radii never shares a cache entry.
    queryKey: [...RANKING_QUERY_KEY, session?.commerce_url, radiusKm],
    queryFn: () => getMyRanking(session!, radiusKm),
    enabled: !!session && radiusKm > 0,
    staleTime: RANKING_REFRESH_MS,
    refetchInterval: RANKING_REFRESH_MS,
    // A background poll while the user's on a different tab is wasted work — the server-side
    // cache holds the same answer for 5 min, and the moment they return `refetchOnWindowFocus`
    // (react-query's default) triggers a fresh read anyway.
    refetchIntervalInBackground: false,
    retry: 1,
  });
}
