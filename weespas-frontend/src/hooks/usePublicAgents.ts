import { useQuery } from '@tanstack/react-query';
import { fetchPublicAgents } from '../api/agents';
import type { PaginatedResponse, PublicAgent } from '../types/propertyApi';

export function usePublicAgents(params: { skip?: number; limit?: number; q?: string }) {
  return useQuery<PaginatedResponse<PublicAgent>, Error>({
    queryKey: ['publicAgents', params.skip, params.limit, params.q],
    queryFn: () => fetchPublicAgents(params),
    staleTime: 1000 * 60 * 2,
    gcTime: 1000 * 60 * 15,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}
