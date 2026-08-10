import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { fetchRelatedProperties } from '../api/properties';
import type { Property } from '../types/propertyApi';

const RELATED_LIMIT = 12;

/**
 * Fetch ranked related properties for a set of source listings.
 *
 * Delegates ranking to the backend `/properties/related` endpoint, which scores
 * candidates by proximity to the source centroid, engagement, and similarity
 * (city + bedrooms). Source IDs are excluded server-side.
 */
export function useRelatedProperties(sourceProperties: Property[]) {
  const sourceIds = useMemo(
    () => sourceProperties.map((p) => p.id).filter(Boolean).sort(),
    [sourceProperties],
  );

  const query = useQuery({
    queryKey: ['relatedProperties', sourceIds],
    queryFn: () => fetchRelatedProperties(sourceIds, RELATED_LIMIT),
    enabled: sourceIds.length > 0,
    staleTime: 1000 * 60 * 5,
    gcTime: 1000 * 60 * 15,
    refetchOnWindowFocus: false,
  });

  return {
    properties: query.data ?? [],
    isLoading: query.isLoading,
  };
}
