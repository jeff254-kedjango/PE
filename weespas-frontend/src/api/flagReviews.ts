// Flag-review queue API client (staff/admin side of "flag a building").
//
// Mirrors src/api/notifications.ts: `fetchJson` + Bearer header. Every endpoint is
// staff/admin-gated server-side; the acknowledger/viewer identity is taken from the
// token, never sent by the client.
import { fetchJson, API_BASE_URL } from './config';

const authHeaders = (token: string) => ({ Authorization: `Bearer ${token}` });

// 1=CLEARED, 2=UNSAFE, 3=AUTH_UNSAFE — mirrors the backend FLAG_* states.
export type FlagReviewState = 1 | 2 | 3;

export interface FlagReview {
  id: string;
  flag_id: string;
  aoi_code: string;
  insar_building_id: number;
  state: FlagReviewState;
  source: string;                  // 'engineer' | 'authority'
  note: string | null;
  observed_at: string | null;      // ISO date
  flagged_at: string | null;       // ISO timestamp
  flagged_by_id: string | null;
  flagged_by_name: string | null;  // who raised the flag
  seen: boolean;
  seen_at: string | null;
  seen_by_id: string | null;
  seen_by_name: string | null;     // who acknowledged it
  views: number;                   // distinct people who viewed it
}

export interface OpenFlagReviewCount {
  count: number;
}

/** Newest-first page of flag reviews. `status` defaults to 'open' (unseen only). */
export async function fetchFlagReviews(
  token: string,
  opts?: { status?: 'open' | 'all'; limit?: number; before?: string },
): Promise<FlagReview[]> {
  const params = new URLSearchParams();
  if (opts?.status) params.set('status', opts.status);
  if (opts?.limit) params.set('limit', String(opts.limit));
  if (opts?.before) params.set('before', opts.before);
  const qs = params.toString() ? `?${params.toString()}` : '';
  return fetchJson<FlagReview[]>(`${API_BASE_URL}/flag-reviews${qs}`, {
    headers: authHeaders(token),
  });
}

/** The staff badge: indexed count of unseen reviews. */
export async function fetchOpenFlagReviewCount(
  token: string,
): Promise<OpenFlagReviewCount> {
  return fetchJson<OpenFlagReviewCount>(`${API_BASE_URL}/flag-reviews/open-count`, {
    headers: authHeaders(token),
  });
}

/** Mark a review seen as the caller (first-wins; the acknowledger is recorded). */
export async function markFlagReviewSeen(
  token: string,
  id: string,
): Promise<FlagReview> {
  return fetchJson<FlagReview>(
    `${API_BASE_URL}/flag-reviews/${encodeURIComponent(id)}/seen`,
    { method: 'POST', headers: authHeaders(token) },
  );
}

/** Record that the caller viewed a review (distinct people). Returns the new count. */
export async function recordFlagReviewView(
  token: string,
  id: string,
): Promise<{ views: number }> {
  return fetchJson<{ views: number }>(
    `${API_BASE_URL}/flag-reviews/${encodeURIComponent(id)}/view`,
    { method: 'POST', headers: authHeaders(token) },
  );
}
