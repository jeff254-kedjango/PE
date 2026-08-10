// useRoleApplicationBadge — pending-count badge for the NavBar's
// Admin Panel link.
//
// Endpoint reads two Redis counters (~0.5 ms server-side), so we can
// poll at 60-second intervals without measurable cost — one cheap GET
// per admin per minute. Compared with a WebSocket push, the polling
// approach avoids a long-lived connection per admin and is robust to
// reconnects with no extra code.
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import { useMe } from './useMe';
import { hasRole } from '../utils/roles';
import {
  fetchRoleApplicationBadge,
  type RoleApplicationBadge,
} from '../api/roleApplications';

export function useRoleApplicationBadge() {
  const { token } = useAuth();
  const { data: me } = useMe();
  const isAdmin = !!me && hasRole(me, 'admin');
  return useQuery<RoleApplicationBadge, Error>({
    queryKey: ['roleApplicationBadge'],
    queryFn: () => fetchRoleApplicationBadge(token!),
    // Admin-only — silently disabled for everyone else so the badge
    // doesn't fire endpoint 403s on every Navbar mount.
    enabled: !!token && isAdmin,
    staleTime: 60_000,
    refetchInterval: 60_000,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}
