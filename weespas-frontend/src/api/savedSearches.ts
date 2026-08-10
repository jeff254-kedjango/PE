// Saved searches API — Phase 3 of Profile_Architecture.md.
// The `filters` object is opaque to the server; it round-trips the same
// shape that `useFilterParams` produces on the client.
import { API_BASE_URL, fetchJson } from './config';

export interface SavedSearch {
  id: string;
  name: string;
  filters: Record<string, unknown>;
  created_at: string;
  last_used_at?: string | null;
}

const authHeaders = (token: string) => ({ Authorization: `Bearer ${token}` });

export async function listSavedSearches(token: string): Promise<SavedSearch[]> {
  return fetchJson<SavedSearch[]>(`${API_BASE_URL}/me/saved-searches`, {
    headers: authHeaders(token),
  });
}

export async function createSavedSearch(
  token: string,
  body: { name: string; filters: Record<string, unknown> },
): Promise<SavedSearch> {
  return fetchJson<SavedSearch>(`${API_BASE_URL}/me/saved-searches`, {
    method: 'POST',
    headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function updateSavedSearch(
  token: string,
  id: string,
  body: { name?: string; filters?: Record<string, unknown>; touch?: boolean },
): Promise<SavedSearch> {
  return fetchJson<SavedSearch>(`${API_BASE_URL}/me/saved-searches/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function deleteSavedSearch(token: string, id: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/me/saved-searches/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: authHeaders(token),
    credentials: 'include',
  });
  if (!res.ok) throw new Error(`delete failed: ${res.status}`);
}
