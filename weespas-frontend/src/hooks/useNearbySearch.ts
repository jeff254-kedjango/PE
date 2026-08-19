import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchGeoProperties } from '../api/properties';
import { PaginatedResponse, Property, PropertyFilterParams } from '../types/propertyApi';

const hasGeoQuery = (params: PropertyFilterParams) => {
  return params.latitude !== undefined && params.longitude !== undefined && params.radius !== undefined;
};

function useDebouncedValue<T>(value: T, delay = 400) {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);

  return debounced;
}

export function useNearbySearch(initialQuery: PropertyFilterParams) {
  const [query, setQuery] = useState<PropertyFilterParams>(initialQuery);
  const debouncedQuery = useDebouncedValue(query, 450);

  const result = useQuery<PaginatedResponse<Property>, Error>({
    queryKey: ['nearbyProperties', JSON.stringify(debouncedQuery)],
    queryFn: () => fetchGeoProperties({
      ...debouncedQuery,
      skip: debouncedQuery.skip ?? 0,
      limit: debouncedQuery.limit ?? 20
    }),
    enabled: hasGeoQuery(debouncedQuery),
    staleTime: 1000 * 60 * 2,
    gcTime: 1000 * 60 * 10,
    refetchOnWindowFocus: false,
    retry: 1
  });

  const properties = result.data?.items ?? [];

  const refresh = () => setQuery((current) => ({ ...current }));

  const controls = useMemo(
    () => ({
      refresh,
      setQuery
    }),
    []
  );

  return {
    properties,
    ...result,
    ...controls
  };
}
