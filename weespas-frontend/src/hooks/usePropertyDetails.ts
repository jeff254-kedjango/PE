import { useQuery } from '@tanstack/react-query';
import { fetchPropertyDetails } from '../api/properties';
import { Property } from '../types/propertyApi';

export function usePropertyDetails(propertyId: string | null) {
  return useQuery<Property, Error>({
    queryKey: ['property', propertyId],
    queryFn: () => {
      if (!propertyId) throw new Error('Property ID required');
      return fetchPropertyDetails(propertyId);
    },
    enabled: Boolean(propertyId),
    staleTime: 1000 * 60 * 2,
    gcTime: 1000 * 60 * 15,
    refetchOnWindowFocus: false,
    retry: 1
  });
}
