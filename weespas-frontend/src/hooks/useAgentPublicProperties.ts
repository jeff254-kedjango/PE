import { useQuery } from '@tanstack/react-query';
import { fetchPublicAgentProperties } from '../api/agents';
import type { PaginatedResponse, Property } from '../types/propertyApi';

export function useAgentPublicProperties(
  agentId: string | undefined,
  params: { skip: number; limit: number }
) {
  return useQuery<PaginatedResponse<Property>, Error>({
    queryKey: ['agentPublicProperties', agentId, params.skip, params.limit],
    queryFn: () => fetchPublicAgentProperties(agentId!, params),
    enabled: Boolean(agentId),
    staleTime: 1000 * 60 * 2,
    gcTime: 1000 * 60 * 15,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}
