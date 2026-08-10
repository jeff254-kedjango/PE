// Notification inbox hooks (the bell badge + dropdown list).
//
// Polling cadence matches useRoleApplicationBadge: the unread-count endpoint is a
// single indexed COUNT (~sub-ms server-side), so a 60-second poll per signed-in user
// is negligible and avoids a long-lived socket. The list is fetched lazily (only when
// the dropdown opens) so a closed bell costs just the count poll.
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import {
  fetchNotifications,
  fetchUnreadCount,
  markNotificationRead,
  markAllNotificationsRead,
  type AppNotification,
  type UnreadCount,
} from '../api/notifications';

const UNREAD_KEY = ['notifications', 'unread-count'];
const LIST_KEY = ['notifications', 'list'];

/** Unread badge count. Polls every 60s while authenticated; disabled otherwise so
 *  it never fires a 401 on an anonymous navbar mount. */
export function useUnreadNotificationCount() {
  const { token, isAuthenticated } = useAuth();
  return useQuery<UnreadCount, Error>({
    queryKey: UNREAD_KEY,
    queryFn: () => fetchUnreadCount(token!),
    enabled: !!token && isAuthenticated,
    staleTime: 60_000,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
    retry: 1,
  });
}

/** The inbox list. `enabled` lets the caller fetch only when the dropdown is open. */
export function useNotificationList(enabled: boolean) {
  const { token, isAuthenticated } = useAuth();
  return useQuery<AppNotification[], Error>({
    queryKey: LIST_KEY,
    queryFn: () => fetchNotifications(token!, { limit: 20 }),
    enabled: !!token && isAuthenticated && enabled,
    staleTime: 30_000,
    retry: 1,
  });
}

/** Mark one read, then refresh the badge + list. */
export function useMarkNotificationRead() {
  const { token } = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => markNotificationRead(token!, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: UNREAD_KEY });
      qc.invalidateQueries({ queryKey: LIST_KEY });
    },
  });
}

/** Mark all read, then refresh the badge + list. */
export function useMarkAllNotificationsRead() {
  const { token } = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => markAllNotificationsRead(token!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: UNREAD_KEY });
      qc.invalidateQueries({ queryKey: LIST_KEY });
    },
  });
}
