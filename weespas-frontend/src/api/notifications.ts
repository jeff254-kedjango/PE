// In-app notification inbox API client.
//
// Mirrors the idiom in src/api/roleApplications.ts: `fetchJson` + Bearer header.
// Every endpoint is server-scoped to the caller — there is no way to request
// another user's inbox from here.
import { fetchJson, API_BASE_URL } from './config';

const authHeaders = (token: string) => ({ Authorization: `Bearer ${token}` });

export type NotificationKind = 'listing_verification' | string;

export interface AppNotification {
  id: string;
  kind: NotificationKind;
  title: string;
  body: string;
  link: string | null;
  read_at: string | null;   // ISO timestamp, or null while unread
  created_at: string | null;
}

export interface UnreadCount {
  count: number;
}

/** Newest-first page of the caller's own notifications. */
export async function fetchNotifications(
  token: string,
  opts?: { limit?: number; before?: string },
): Promise<AppNotification[]> {
  const params = new URLSearchParams();
  if (opts?.limit) params.set('limit', String(opts.limit));
  if (opts?.before) params.set('before', opts.before);
  const qs = params.toString() ? `?${params.toString()}` : '';
  return fetchJson<AppNotification[]>(`${API_BASE_URL}/notifications${qs}`, {
    headers: authHeaders(token),
  });
}

/** The bell badge: indexed count of unread notifications. */
export async function fetchUnreadCount(token: string): Promise<UnreadCount> {
  return fetchJson<UnreadCount>(`${API_BASE_URL}/notifications/unread-count`, {
    headers: authHeaders(token),
  });
}

/** Mark one of the caller's own notifications read (204 No Content). */
export async function markNotificationRead(token: string, id: string): Promise<void> {
  await fetch(`${API_BASE_URL}/notifications/${encodeURIComponent(id)}/read`, {
    method: 'POST',
    credentials: 'include',
    headers: authHeaders(token),
  });
}

/** Mark every unread notification of the caller read. */
export async function markAllNotificationsRead(token: string): Promise<{ marked: number }> {
  return fetchJson<{ marked: number }>(`${API_BASE_URL}/notifications/read-all`, {
    method: 'POST',
    headers: authHeaders(token),
  });
}
