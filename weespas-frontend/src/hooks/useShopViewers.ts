// useShopViewers — the Viewing Card's data source (§8, Chunk C).
//
// Three hooks bundled:
//   * useShopLiveCount    — polls the live-count endpoint every 30s (matches heartbeat cadence)
//   * useShopViewHistory  — keyset-paginated infinite query over past visits, with calendar
//                           filter (since / until)
//   * usePromoteAllShop   — mutation that boosts every active in-stock listing on the shop; the
//                           mutation invalidates the seller's storefront so the dashboard shows
//                           the fresh promo-mode on each listing.
//
// All three are owner-only (require a session). They share nothing intentionally — a caller can
// mount just the live count without the history hook firing, etc.
import {
  useQuery,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/react-query';
import {
  getShopLiveCount,
  getShopLiveViewers,
  getShopViewHistory,
  promoteAllShopListings,
  type CommerceSession,
  type LiveCountOut,
  type LiveViewersOut,
  type ViewHistoryOut,
  type PromoteAllOut,
} from '../api/commerce';
import { MY_STOREFRONT_KEY } from './useMyStorefront';

/** Poll cadence for the live count. 30s = same rhythm as the heartbeat; polling faster would
 *  just hit stale state (a viewer who left 5 seconds ago is still "live" until their 60s window
 *  lapses on the server). */
export const LIVE_COUNT_POLL_MS = 30_000;

export const SHOP_LIVE_COUNT_KEY = ['commerce', 'shop', 'live-count'] as const;
export const SHOP_LIVE_VIEWERS_KEY = ['commerce', 'shop', 'live-viewers'] as const;
export const SHOP_VIEW_HISTORY_KEY = ['commerce', 'shop', 'view-history'] as const;

export function useShopLiveCount(session: CommerceSession | null, shopId: string | null) {
  return useQuery<LiveCountOut, Error>({
    queryKey: [...SHOP_LIVE_COUNT_KEY, session?.commerce_url, shopId],
    queryFn: () => getShopLiveCount(session!, shopId!),
    enabled: !!session && !!shopId,
    // A cache-hit within the polling window is fine — the server's freshness definition (60s)
    // means a slightly-stale count is normal.
    staleTime: LIVE_COUNT_POLL_MS,
    refetchInterval: LIVE_COUNT_POLL_MS,
    refetchIntervalInBackground: false,   // no polling on a hidden tab
    retry: 1,
  });
}

/** §8 Chunk C+ — hydrated live viewers for the Viewing Card. Same polling cadence as the
 *  live-count hook: the response includes the count so the card can bind BOTH the (N)
 *  counter next to the header AND the row list from ONE query, no split-brain. */
export function useShopLiveViewers(session: CommerceSession | null, shopId: string | null) {
  return useQuery<LiveViewersOut, Error>({
    queryKey: [...SHOP_LIVE_VIEWERS_KEY, session?.commerce_url, shopId],
    queryFn: () => getShopLiveViewers(session!, shopId!),
    enabled: !!session && !!shopId,
    staleTime: LIVE_COUNT_POLL_MS,
    refetchInterval: LIVE_COUNT_POLL_MS,
    refetchIntervalInBackground: false,
    retry: 1,
  });
}

interface ViewHistoryFilters {
  /** ISO datetime; null ⇒ no lower bound. */
  since: string | null;
  /** ISO datetime; null ⇒ no upper bound. */
  until: string | null;
}

export function useShopViewHistory(
  session: CommerceSession | null,
  shopId: string | null,
  filters: ViewHistoryFilters,
) {
  return useInfiniteQuery<
    ViewHistoryOut,
    Error,
    InfiniteData<ViewHistoryOut, string | null>,
    readonly unknown[],
    string | null
  >({
    // `since`/`until` participate in the key: a calendar change is a fresh fetch, not a
    // stale-render of the old range.
    queryKey: [...SHOP_VIEW_HISTORY_KEY, session?.commerce_url, shopId, filters.since, filters.until],
    queryFn: ({ pageParam }) => getShopViewHistory(session!, shopId!, {
      since: filters.since,
      until: filters.until,
      cursor: pageParam,
    }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    initialPageParam: null,
    enabled: !!session && !!shopId,
    // History is append-only past data — a 60s stale window matches the storefront cache TTL.
    staleTime: 60_000,
    retry: 1,
  });
}

interface PromoteAllArgs {
  shopId: string;
  durationSeconds: number;
}

export function usePromoteAllShop(session: CommerceSession | null) {
  const qc = useQueryClient();
  return useMutation<PromoteAllOut, Error, PromoteAllArgs>({
    mutationFn: ({ shopId, durationSeconds }) => promoteAllShopListings(session!, shopId, durationSeconds),
    onSuccess: () => {
      // The seller's storefront dashboard renders per-listing `is_promoted` badges — invalidate
      // so the freshly-promoted state shows up without waiting for the 30s staleTime.
      qc.invalidateQueries({ queryKey: MY_STOREFRONT_KEY });
    },
  });
}
