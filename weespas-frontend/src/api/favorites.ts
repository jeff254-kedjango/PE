import { API_BASE_URL, fetchJson } from './config';

interface FavoriteRow {
  id: string;
  property_id: string;
  created_at: string;
}

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

export function listFavorites(token: string) {
  return fetchJson<FavoriteRow[]>(`${API_BASE_URL}/favorites/me`, {
    headers: authHeaders(token),
    credentials: 'include',
  });
}

export function addFavorite(token: string, propertyId: string) {
  return fetchJson<FavoriteRow>(`${API_BASE_URL}/favorites`, {
    method: 'POST',
    headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ property_id: propertyId }),
  });
}

export async function removeFavorite(token: string, propertyId: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/favorites/${propertyId}`, {
    method: 'DELETE',
    headers: authHeaders(token),
    credentials: 'include',
  });
  if (!res.ok && res.status !== 404) {
    throw new Error(`Failed to remove favorite: ${res.status}`);
  }
}
