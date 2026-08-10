import { useQuery } from '@tanstack/react-query';
import { listDeletionRequests } from '../api/admin';
import type { DeletionRequest } from '../types/admin';
import type { PaginatedResponse } from '../types/propertyApi';

export function useDeletionRequests(token: string | null, statusFilter = 'pending') {
  return useQuery<PaginatedResponse<DeletionRequest>, Error>({
    queryKey: ['deletionRequests', statusFilter],
    queryFn: () => {
      if (!token) throw new Error('Token required');
      return listDeletionRequests(token, { status: statusFilter });
    },
    enabled: Boolean(token),
    staleTime: 1000 * 60 * 2,
    gcTime: 1000 * 60 * 15,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}
