import { useQuery } from '@tanstack/react-query';
import { listStaffDeletionRequests } from '../api/admin';
import type { DeletionRequest } from '../types/admin';
import type { PaginatedResponse } from '../types/propertyApi';

export function useStaffDeletionRequests(token: string | null) {
  return useQuery<PaginatedResponse<DeletionRequest>, Error>({
    queryKey: ['staffDeletionRequests'],
    queryFn: () => {
      if (!token) throw new Error('Token required');
      return listStaffDeletionRequests(token);
    },
    enabled: Boolean(token),
    staleTime: 1000 * 60 * 2,
    gcTime: 1000 * 60 * 15,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}
