// Proximity feed hook — the buyer's infinite "what's selling near me" stream.
//
// Pages by the commerce keyset cursor (FeedResponse.next_cursor, id-anchored — see commerce
// services/feed.py): each page passes the previous page's cursor, and pagination ends when the
// backend returns next_cursor=null. Only the FIRST page carries the §8.3 sponsored slots (the
// backend interleaves them there so the keyset stays exact), which is exactly what an
// append-only infinite scroll wants — sponsored items appear once near the top, never duplicated
// as the user pages down.
import { useInfiniteQuery, type InfiniteData } from '@tanstack/react-query';
import { getFeed, type FeedItem, type FeedResponse, type CommerceSession, type FeedKind } from '../api/commerce';

export interface UseCommerceFeedArgs {
  session: CommerceSession | null;
  lat: number | null;
  lng: number | null;
  radiusM?: number;
  pageSize?: number;
  /** §8 Listings|Videos toggle. undefined ⇒ both kinds (the unified feed). */
  kind?: FeedKind;
}

export function useCommerceFeed({ session, lat, lng, radiusM, pageSize, kind }: UseCommerceFeedArgs) {
  const enabled = !!session && lat != null && lng != null;

  const query = useInfiniteQuery<
    FeedResponse,
    Error,
    InfiniteData<FeedResponse, string | null>,
    readonly unknown[],
    string | null
  >({
    // Keyed by location + radius + kind so moving the pin, changing radius, OR flipping the
    // Listings/Videos toggle each yields a distinct, independently-cached feed.
    queryKey: ['commerce', 'feed', session?.commerce_url, lat, lng, radiusM ?? null, kind ?? null],
    queryFn: ({ pageParam }) =>
      getFeed(session!, {
        lat: lat!,
        lng: lng!,
        radius_m: radiusM,
        cursor: pageParam,
        limit: pageSize,
        kind,
      }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    initialPageParam: null,
    enabled,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    retry: 1,
  });

  const items: FeedItem[] = query.data?.pages.flatMap((p) => p.items) ?? [];

  // Auto-widen signals live on every page (a sparse area's local search is empty on each page, so
  // the backend widens uniformly), but the FIRST page is the stable source of truth — reading it
  // avoids any flicker as later pages load. undefined until the first page resolves.
  const firstPage = query.data?.pages[0];
  const widened = firstPage?.widened ?? false;
  const nearestDistanceM = firstPage?.nearest_distance_m ?? null;
  // How many the immediate (un-widened) radius held — lets the widen note distinguish "nothing
  // nearby" (0) from "only a few nearby, also showing farther" (>0) honestly.
  const immediateCount = firstPage?.immediate_count ?? 0;

  return { ...query, items, widened, nearestDistanceM, immediateCount };
}
