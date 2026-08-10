import { useQuery } from '@tanstack/react-query';
import { fetchPublicAgentById } from '../api/agents';
import type { PublicAgent } from '../types/propertyApi';

export function useAgentProfile(agentId: string | undefined) {
  return useQuery<PublicAgent, Error>({
    queryKey: ['agentProfile', agentId],
    queryFn: () => fetchPublicAgentById(agentId!),
    enabled: Boolean(agentId),
    staleTime: 1000 * 60 * 2,
    gcTime: 1000 * 60 * 15,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}
