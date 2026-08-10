// useQuickBuys — fetches the buyer's Quick Buys grid (§8 Trade right-rail 3×3 discovery grid).
//
// Mirrors useCommerceFeed/useCommerceSession: a react-query read keyed by the commerce base + the
// buyer's coordinates + the active filters, enabled only once a session + coordinates exist. The
// result is PERSONAL (the server reads the caller's own engagement history for affinity), so it is
// never shared across users; react-query's per-user session key + AuthContext's cache wipe keep it
// isolated. A short staleTime (the grid is a lightweight discovery surface, not a live feed).
import { useQuery } from '@tanstack/react-query';
import {
  getQuickBuys,
  type CommerceSession,
  type QuickBuysFilters,
  type QuickBuysResponse,
} from '../api/commerce';

const QUICK_BUYS_STALE_MS = 60_000;

interface UseQuickBuysArgs {
  session: CommerceSession | null;
  lat: number | null;
  lng: number | null;
  filters?: QuickBuysFilters;
}

export interface UseQuickBuys {
  data: QuickBuysResponse | null;
  isLoading: boolean;
  isError: boolean;
}

export function useQuickBuys({ session, lat, lng, filters }: UseQuickBuysArgs): UseQuickBuys {
  // Stable, serialisable filter key so a filter change refetches but an identical object reference
  // change does not. Undefined fields collapse to an empty object → a stable key for "no filters".
  const filterKey = JSON.stringify({
    p0: filters?.minPriceCents ?? null,
    p1: filters?.maxPriceCents ?? null,
    c: filters?.categories ?? [],
    r: filters?.radiusM ?? null,
  });

  const { data, isLoading, isError } = useQuery<QuickBuysResponse, Error>({
    queryKey: ['commerce', 'quick-buys', session?.commerce_url ?? 'none', lat, lng, filterKey],
    queryFn: () => getQuickBuys(session!, lat!, lng!, filters),
    enabled: !!session && lat != null && lng != null,
    staleTime: QUICK_BUYS_STALE_MS,
    retry: 1,
  });

  return { data: data ?? null, isLoading, isError };
}
