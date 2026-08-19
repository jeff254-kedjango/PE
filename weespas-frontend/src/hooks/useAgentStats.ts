import { useQuery } from '@tanstack/react-query';
import { fetchAgentStats } from '../api/agents';
import type { AgentStats } from '../types/stats';

export function useAgentStats(
  token: string | null,
  scope: 'mine' | 'global' = 'mine'
) {
  return useQuery<AgentStats, Error>({
    queryKey: ['agentStats', token ?? 'anon', scope],
    queryFn: () => {
      if (!token) throw new Error('Token required');
      return fetchAgentStats(token, scope);
    },
    enabled: Boolean(token),
    staleTime: 1000 * 60 * 2,
    gcTime: 1000 * 60 * 15,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}
