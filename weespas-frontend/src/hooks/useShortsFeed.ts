import { useInfiniteQuery, type InfiniteData } from '@tanstack/react-query';
import { fetchShortsFeed, type PaginatedShorts } from '../api/shorts';

const DEFAULT_PAGE_SIZE = 10;

export function useShortsFeed(
  token: string | null = null,
  pageSize: number = DEFAULT_PAGE_SIZE,
  // When false, the query is disabled (no real-estate shorts fetch). Used by VerticalVideoFeed's
  // controlled mode — a caller (e.g. the commerce Trade strip) supplies its OWN items, so the
  // hook must still be called (rules of hooks) but must not fetch the property-shorts feed.
  enabled: boolean = true,
) {
  const query = useInfiniteQuery<PaginatedShorts, Error, InfiniteData<PaginatedShorts, number>, readonly unknown[], number>({
    queryKey: ['shorts-feed', token],
    queryFn: ({ pageParam = 0 }) => fetchShortsFeed({ skip: pageParam, limit: pageSize, token }),
    getNextPageParam: (lastPage) => {
      const next = lastPage.skip + lastPage.limit;
      return next < lastPage.total ? next : undefined;
    },
    initialPageParam: 0,
    enabled,
    // Align with backend feed_cache_ttl=300s.
    staleTime: 1000 * 60 * 5,
    gcTime: 1000 * 60 * 15,
    refetchOnWindowFocus: false,
    retry: 1,
  });

  const items = query.data?.pages.flatMap((p) => p.items) ?? [];
  const total = query.data?.pages[0]?.total ?? 0;

  return { ...query, items, total };
}
