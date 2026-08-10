export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api/v1';

// NOTE: InSAR is now free-but-login-required, so there is no anonymous "public map" URL
// to open from the frontend any more. Authed users get the InSAR deep-link from the
// backend (settings.insar_public_url, returned by /insar/session-token — see api/insar.ts);
// anonymous clicks route to /login?next=insar and resume the map after sign-in.

// Default `credentials: 'include'` so the `weespas_session` cookie survives
// across every API call. Without this, calls that omit `credentials` (the
// browser default is `same-origin`, which drops cookies on cross-origin
// requests) cause the backend SessionMiddleware to mint a fresh session row
// for each request — breaking /analytics/engagement (return_interval ≈ 0,
// avg_usage_minutes = 0). Caller-provided `credentials` still wins.
export async function fetchJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, { credentials: 'include', ...init });

  if (response.status === 401) {
    // Token expired or invalid — clear stale session so user is prompted to re-login
    localStorage.removeItem('weespas_token');
    localStorage.removeItem('weespas_user');
    window.location.href = '/login';
    throw new Error('Session expired. Please log in again.');
  }

  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Request failed: ${response.status} ${response.statusText} - ${errorText}`);
  }

  return response.json() as Promise<T>;
}
