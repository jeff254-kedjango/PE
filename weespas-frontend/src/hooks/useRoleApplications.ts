// useRoleApplications — admin queue list, filterable by status and role.
//
// Same pattern as useDeletionRequests. Polling is OFF; the admin
// invalidates the query when they approve/reject and the badge hook
// covers passive "new application arrived" awareness.
import { useQuery } from '@tanstack/react-query';
import {
  listRoleApplications,
  type PaginatedRoleApplications,
  type RoleApplicationRole,
  type RoleApplicationStatus,
} from '../api/roleApplications';

export function useRoleApplications(
  token: string | null,
  params: {
    status?: RoleApplicationStatus;
    role?: RoleApplicationRole;
    skip?: number;
    limit?: number;
  } = {},
) {
  return useQuery<PaginatedRoleApplications, Error>({
    queryKey: ['roleApplications', params.status ?? 'all', params.role ?? 'all', params.skip ?? 0, params.limit ?? 20],
    queryFn: () => {
      if (!token) throw new Error('Token required');
      return listRoleApplications(token, params);
    },
    enabled: !!token,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}
