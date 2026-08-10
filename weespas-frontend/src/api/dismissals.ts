import { API_BASE_URL, fetchJson } from './config';

interface DismissalRow {
  property_id: string;
}

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

export function listDismissals(token: string) {
  return fetchJson<DismissalRow[]>(`${API_BASE_URL}/dismissals/me`, {
    headers: authHeaders(token),
    credentials: 'include',
  });
}

export function addDismissal(token: string, propertyId: string) {
  return fetchJson<DismissalRow>(`${API_BASE_URL}/dismissals/${propertyId}`, {
    method: 'POST',
    headers: authHeaders(token),
    credentials: 'include',
  });
}

export async function removeDismissal(token: string, propertyId: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/dismissals/${propertyId}`, {
    method: 'DELETE',
    headers: authHeaders(token),
    credentials: 'include',
  });
  if (!res.ok && res.status !== 404) {
    throw new Error(`Failed to undo dismissal: ${res.status}`);
  }
}
