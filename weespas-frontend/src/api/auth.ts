// Auth API helpers — `GET /auth/me` and the partial-update PATCH for Phase 0
// of the Profile Architecture. AuthContext owns the bearer token and the
// localStorage cache; React Query (`useMe`) owns the in-memory cache key
// `['auth', 'me']` so every screen that needs fresh user data subscribes to
// the same source of truth without a network round-trip per consumer.
import { API_BASE_URL, fetchJson } from './config';
import type { User } from '../types/auth';

export interface UserUpdateRequest {
  name?: string;
  avatar?: string;
  is_public_profile?: boolean;
  // Phase 6 — notification prefs (forward-compatible: backend ignores fields it doesn't know yet).
  notify_inquiries_sms?: boolean;
  notify_inquiries_email?: boolean;
  notify_digest_email?: boolean;
  notify_push?: boolean;
  // Phase 8 — search defaults
  default_radius_km?: number | null;
  preferred_listing_type?: 'rent' | 'sale' | null;
  preferred_categories?: string[] | null;
  language?: 'en' | 'sw' | null;
}

export interface AvatarUploadResponse {
  url: string;
  thumbnail_url: string;
}

const authHeaders = (token: string) => ({
  Authorization: `Bearer ${token}`,
});

export async function fetchMe(token: string): Promise<User> {
  return fetchJson<User>(`${API_BASE_URL}/auth/me`, {
    headers: authHeaders(token),
  });
}

export async function updateMe(
  token: string,
  patch: UserUpdateRequest,
): Promise<User> {
  return fetchJson<User>(`${API_BASE_URL}/auth/me`, {
    method: 'PATCH',
    headers: {
      ...authHeaders(token),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(patch),
  });
}

/** Update the authenticated agent's bio. Writes to agents.bio on the
 *  backend (not users — that's a different table). Backend rejects with
 *  403 when the caller has no agent_id. Empty string clears the bio. */
export async function updateBio(token: string, bio: string): Promise<{ bio: string }> {
  return fetchJson<{ bio: string }>(`${API_BASE_URL}/me/bio`, {
    method: 'PATCH',
    headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
    body: JSON.stringify({ bio }),
  });
}

/** Upload a new avatar. Returns the URL to PATCH onto /auth/me. The
 *  backend writes the URL to users.avatar synchronously and serves the
 *  file under /uploads/avatars/. */
export async function uploadAvatar(
  token: string,
  file: File,
): Promise<AvatarUploadResponse> {
  const form = new FormData();
  form.append('file', file);
  // Note: do NOT set Content-Type — the browser appends the multipart
  // boundary automatically. Setting it manually corrupts the request body.
  return fetchJson<AvatarUploadResponse>(`${API_BASE_URL}/me/avatar`, {
    method: 'POST',
    headers: authHeaders(token),
    body: form,
  });
}

// ────────────────────────────────────────────────────────────────────
// Phase 4 — hidden listings
// ────────────────────────────────────────────────────────────────────
export interface HiddenListingItem {
  property_id: string;
  title?: string | null;
  price?: number | null;
  city?: string | null;
  main_image_url?: string | null;
  listing_type?: string | null;
  dismissed_at?: string | null;
}

export async function fetchHiddenListings(token: string): Promise<HiddenListingItem[]> {
  return fetchJson<HiddenListingItem[]>(`${API_BASE_URL}/me/dismissals`, {
    headers: authHeaders(token),
  });
}

export async function unhideAllDismissals(token: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/me/dismissals`, {
    method: 'DELETE',
    headers: authHeaders(token),
    credentials: 'include',
  });
  if (!res.ok) throw new Error(`unhide-all failed: ${res.status}`);
}

// ────────────────────────────────────────────────────────────────────
// Phase 5 — active sessions
// ────────────────────────────────────────────────────────────────────
export interface ActiveSessionItem {
  id: string;
  user_agent?: string | null;
  ip_address?: string | null;
  geo_city?: string | null;
  geo_county?: string | null;
  last_seen_at?: string | null;
  created_at?: string | null;
  is_current: boolean;
}

export async function fetchSessions(token: string): Promise<ActiveSessionItem[]> {
  return fetchJson<ActiveSessionItem[]>(`${API_BASE_URL}/me/sessions`, {
    headers: authHeaders(token),
  });
}

export async function revokeSession(token: string, id: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/me/sessions/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: authHeaders(token),
    credentials: 'include',
  });
  if (!res.ok) throw new Error(`revoke session failed: ${res.status}`);
}

export async function revokeAllOtherSessions(token: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/me/sessions`, {
    method: 'DELETE',
    headers: authHeaders(token),
    credentials: 'include',
  });
  if (!res.ok) throw new Error(`revoke-all failed: ${res.status}`);
}

// ────────────────────────────────────────────────────────────────────
// Phase 7 — change password + delete account
// ────────────────────────────────────────────────────────────────────
export async function changePassword(
  token: string,
  oldPassword: string,
  newPassword: string,
): Promise<{ ok: true }> {
  return fetchJson<{ ok: true }>(`${API_BASE_URL}/auth/change-password`, {
    method: 'POST',
    headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
}

export async function requestSelfDeletion(
  token: string,
  reason: string,
): Promise<{ id: string; status: string; created_at: string }> {
  return fetchJson(`${API_BASE_URL}/me/deletion-request`, {
    method: 'POST',
    headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  });
}

// ────────────────────────────────────────────────────────────────────
// Phase 9 — phone / email change (OTP-gated)
// ────────────────────────────────────────────────────────────────────
export async function startPhoneChange(token: string, newPhone: string) {
  return fetchJson<{ ok: true; expires_in: number }>(
    `${API_BASE_URL}/me/phone/start-change`,
    {
      method: 'POST',
      headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_phone: newPhone }),
    },
  );
}

export async function confirmPhoneChange(token: string, otp: string) {
  return fetchJson<User>(`${API_BASE_URL}/me/phone/confirm`, {
    method: 'POST',
    headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
    body: JSON.stringify({ otp }),
  });
}

export async function startEmailChange(token: string, newEmail: string) {
  return fetchJson<{ ok: true; expires_in: number }>(
    `${API_BASE_URL}/me/email/start-change`,
    {
      method: 'POST',
      headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_email: newEmail }),
    },
  );
}

export async function confirmEmailChange(token: string, otp: string) {
  return fetchJson<User>(`${API_BASE_URL}/me/email/confirm`, {
    method: 'POST',
    headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
    body: JSON.stringify({ otp }),
  });
}
