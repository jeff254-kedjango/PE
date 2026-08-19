// Auth types for Weespas

// Primary roles live in the native `role` column; the relationship roles
// (professional / property_owner / tenant / authority) arrive in `roles[]`
// from the backend's multi-role table. Both are surfaced as UserRole here.
export type UserRole =
  | 'user'
  | 'agent'
  | 'staff'
  | 'admin'
  | 'professional'
  | 'property_owner'
  | 'tenant'
  | 'authority';

export interface User {
  id: string;
  name: string;
  email: string;
  phone: string;
  avatar?: string;
  role?: UserRole;
  roles?: UserRole[];
  agent_id?: string | null;
  is_public_profile?: boolean;
  // Phase 6 — notification prefs
  notify_inquiries_sms?: boolean;
  notify_inquiries_email?: boolean;
  notify_digest_email?: boolean;
  notify_push?: boolean;
  // Phase 8 — search defaults
  default_radius_km?: number | null;
  preferred_listing_type?: 'rent' | 'sale' | null;
  language?: 'en' | 'sw' | null;
  // Agent bio — sourced from agents.bio when agent_id is set; null otherwise.
  // Backend populates this in GET/PATCH /auth/me via a scalar query that runs
  // only for agents, so non-agents pay zero extra DB cost.
  bio?: string | null;
  created_at: string;
}

export interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
}

export type LoginMethod = 'phone' | 'email';

export interface LoginCredentials {
  email?: string;
  password?: string;
  phone?: string;
}

export interface OtpPayload {
  phone: string;
  otp: string;
}

export interface RegisterCredentials {
  name: string;
  email: string;
  phone: string;
  password: string;
}
