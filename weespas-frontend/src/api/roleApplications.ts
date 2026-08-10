// Role-application API client.
//
// Mirrors the idiom used by src/api/admin.ts and src/api/auth.ts:
// `fetchJson` + Bearer-token header, URLSearchParams for query strings,
// typed responses returned directly from the fetch helper.
import { fetchJson, API_BASE_URL } from './config';

// ── Types ────────────────────────────────────────────────────────────
export type RoleApplicationRole = 'agent' | 'staff';
export type RoleApplicationStatus = 'pending' | 'approved' | 'rejected';

export interface StaffStats {
  listings: number;
  views: number;
  days: number;
  min_listings: number;
  min_views: number;
  min_days: number;
}

export interface RoleEligibility {
  agent_eligible: boolean;
  staff_eligible: boolean;
  staff_stats?: StaffStats | null;
  pending_agent: boolean;
  pending_staff: boolean;
}

export interface RoleApplication {
  id: string;
  applicant_id: string;
  applicant_name?: string | null;
  role_requested: RoleApplicationRole;
  message: string;
  status: RoleApplicationStatus;
  review_note?: string | null;
  reviewed_by_name?: string | null;
  created_at: string;
  reviewed_at?: string | null;
}

export interface PaginatedRoleApplications {
  total: number;
  skip: number;
  limit: number;
  items: RoleApplication[];
}

export interface RoleApplicationBadge {
  agent_pending: number;
  staff_pending: number;
}

// ── Endpoints ────────────────────────────────────────────────────────
const authHeaders = (token: string) => ({ Authorization: `Bearer ${token}` });

/** Hot-path eligibility check — backs the ProfilePage CTAs.
 *  One Redis HGET on the server; cached client-side for 60s by the hook. */
export async function fetchRoleEligibility(token: string): Promise<RoleEligibility> {
  return fetchJson<RoleEligibility>(`${API_BASE_URL}/me/role-eligibility`, {
    headers: authHeaders(token),
  });
}

export async function submitRoleApplication(
  token: string,
  role: RoleApplicationRole,
  message: string,
): Promise<RoleApplication> {
  return fetchJson<RoleApplication>(`${API_BASE_URL}/me/role-applications`, {
    method: 'POST',
    headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
    body: JSON.stringify({ role_requested: role, message }),
  });
}

export async function listMyRoleApplications(token: string): Promise<RoleApplication[]> {
  return fetchJson<RoleApplication[]>(`${API_BASE_URL}/me/role-applications`, {
    headers: authHeaders(token),
  });
}

export async function listRoleApplications(
  token: string,
  params: {
    status?: RoleApplicationStatus;
    role?: RoleApplicationRole;
    skip?: number;
    limit?: number;
  } = {},
): Promise<PaginatedRoleApplications> {
  const qs = new URLSearchParams();
  qs.set('skip', String(params.skip ?? 0));
  qs.set('limit', String(params.limit ?? 20));
  if (params.status) qs.set('status', params.status);
  if (params.role) qs.set('role', params.role);
  return fetchJson<PaginatedRoleApplications>(
    `${API_BASE_URL}/admin/role-applications?${qs}`,
    { headers: authHeaders(token) },
  );
}

export async function reviewRoleApplication(
  token: string,
  applicationId: string,
  decision: { status: 'approved' | 'rejected'; review_note?: string },
): Promise<RoleApplication> {
  return fetchJson<RoleApplication>(
    `${API_BASE_URL}/admin/role-applications/${applicationId}`,
    {
      method: 'PATCH',
      headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
      body: JSON.stringify(decision),
    },
  );
}

export async function fetchRoleApplicationBadge(
  token: string,
): Promise<RoleApplicationBadge> {
  return fetchJson<RoleApplicationBadge>(
    `${API_BASE_URL}/admin/role-applications/badge`,
    { headers: authHeaders(token) },
  );
}
