import { useQuery } from '@tanstack/react-query';
import { searchProperties } from '../api/properties';
import { PaginatedResponse, Property } from '../types/propertyApi';

export function useTextSearch(query: string) {
  const trimmed = query.trim();

  const result = useQuery<PaginatedResponse<Property>, Error>({
    queryKey: ['textSearch', trimmed],
    queryFn: () => searchProperties(trimmed),
    enabled: trimmed.length >= 2,
    staleTime: 1000 * 60 * 2,
    gcTime: 1000 * 60 * 15,
    retry: 1,
    refetchOnWindowFocus: false,
  });

  const properties = result.data?.items ?? [];
  const total = result.data?.total ?? 0;

  return {
    ...result,
    properties,
    total,
  };
}
