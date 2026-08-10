import { useQuery } from '@tanstack/react-query';
import { fetchPropertyList } from '../api/properties';
import type { PaginatedResponse, Property } from '../types/propertyApi';

export function useAllProperties(
  enabled: boolean,
  params?: { skip?: number; limit?: number }
) {
  const skip = params?.skip ?? 0;
  const limit = params?.limit ?? 10;

  return useQuery<PaginatedResponse<Property>, Error>({
    queryKey: ['allProperties', skip, limit],
    queryFn: () => fetchPropertyList({ skip, limit }),
    enabled,
    staleTime: 1000 * 60 * 2,
    gcTime: 1000 * 60 * 15,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}
