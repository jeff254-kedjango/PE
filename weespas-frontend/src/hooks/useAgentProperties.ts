import { useQuery } from '@tanstack/react-query';
import { fetchAgentProperties } from '../api/agents';
import type { PaginatedResponse, Property } from '../types/propertyApi';

export function useAgentProperties(
  token: string | null,
  params?: { skip?: number; limit?: number }
) {
  const skip = params?.skip ?? 0;
  const limit = params?.limit ?? 10;

  return useQuery<PaginatedResponse<Property>, Error>({
    queryKey: ['agentProperties', skip, limit],
    queryFn: () => {
      if (!token) throw new Error('Token required');
      return fetchAgentProperties(token, { skip, limit });
    },
    enabled: Boolean(token),
    staleTime: 1000 * 60 * 2,
    gcTime: 1000 * 60 * 15,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}
