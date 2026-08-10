// useRoleEligibility — subscribes the ProfilePage to the backend's
// eligibility precompute. Server-side this is a single Redis HGET, so
// the client cost is just one tiny GET per minute per profile mount.
//
// Cache shape:
//   queryKey: ['auth','roleEligibility']
//   staleTime: 60s   — eligibility moves slowly (one day-boundary tick
//                      at most), so we don't burn requests refetching.
//   refetchOnWindowFocus: false — same rationale.
//
// Invalidate this key after a successful submitRoleApplication() so the
// "Become …" CTA is hidden as soon as the application lands.
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import { fetchRoleEligibility, type RoleEligibility } from '../api/roleApplications';

export const ROLE_ELIGIBILITY_KEY = ['auth', 'roleEligibility'] as const;

export function useRoleEligibility() {
  const { token } = useAuth();
  return useQuery<RoleEligibility, Error>({
    queryKey: ROLE_ELIGIBILITY_KEY,
    queryFn: () => fetchRoleEligibility(token!),
    enabled: !!token,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}
