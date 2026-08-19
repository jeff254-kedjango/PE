import { useInfiniteQuery, type InfiniteData } from '@tanstack/react-query';
import { fetchPropertyList, filterProperties } from '../api/properties';
import { PaginatedResponse, Property, PropertyFilterParams } from '../types/propertyApi';

const DEFAULT_PAGE_SIZE = 12;

// Only true *advanced filters* should bypass the personalized /properties feed.
// sort_by / sort_order are intentionally excluded: the home defaults to
// created_at/desc, and the personalized ranker already weighs freshness — if
// we route the default-sorted home to /properties/filter we skip personalization
// entirely (which was the bug: every visitor saw the same listings).
//
// Non-default sorts (price, distance, etc.) are an explicit user intent override,
// so those still bypass personalization via the filter endpoint.
const hasAdvancedFilters = (filters: PropertyFilterParams) => {
  return Boolean(
    filters.latitude !== undefined ||
    filters.longitude !== undefined ||
    filters.listing_type ||
    (filters.category && filters.category !== 'all') ||
    filters.min_price !== undefined ||
    filters.max_price !== undefined ||
    filters.engineer_certified !== undefined ||
    filters.bedrooms !== undefined ||
    filters.bathrooms !== undefined ||
    filters.query
  );
};

const hasNonDefaultSort = (filters: PropertyFilterParams) => {
  if (filters.sort_by && filters.sort_by !== 'created_at') return true;
  if (filters.sort_order && filters.sort_order !== 'desc') return true;
  return false;
};

const shouldUseFilterEndpoint = (filters: PropertyFilterParams) => {
  return hasAdvancedFilters(filters) || hasNonDefaultSort(filters);
};

export function usePropertySearch(
  filters: PropertyFilterParams = {},
  token: string | null = null,
) {
  const normalizedFilters: PropertyFilterParams = {
    ...filters,
    skip: 0,
    limit: filters.limit ?? DEFAULT_PAGE_SIZE
  };
  const useFilter = shouldUseFilterEndpoint(filters);
  // Token participates in the cache key so the feed reorders the moment a user
  // logs in/out (filter-endpoint requests are token-independent on the server).
  const key = ['properties', JSON.stringify(normalizedFilters), useFilter ? null : token];

  const query = useInfiniteQuery<PaginatedResponse<Property>, Error, InfiniteData<PaginatedResponse<Property>, number>, readonly unknown[], number>({
    queryKey: key,
    queryFn: async ({ pageParam = 0 }: { pageParam?: number }) => {
      const request: PropertyFilterParams = { ...normalizedFilters, skip: pageParam };
      return useFilter
        ? filterProperties(request)
        : fetchPropertyList({ skip: request.skip, limit: request.limit, token });
    },
    getNextPageParam: (lastPage) => {
      const nextPage = lastPage.skip + lastPage.limit;
      return nextPage < lastPage.total ? nextPage : undefined;
    },
    initialPageParam: 0,
    // Match the backend personalized-feed TTL (feed_cache_ttl = 300s) so we
    // don't hammer the API faster than the server can rebuild rankings, while
    // still re-fetching on mount after the window expires.
    staleTime: 1000 * 60 * 5,
    gcTime: 1000 * 60 * 15,
    refetchOnWindowFocus: false,
    retry: 1
  });

  const pages = query.data?.pages ?? [];
  const properties = pages.flatMap((page) => page.items);
  const total = pages[0]?.total ?? 0;

  return {
    ...query,
    pages,
    properties,
    total
  };
}
