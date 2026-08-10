import { fetchJson, API_BASE_URL } from './config';
import type {
  PaginatedResponse, PublicAgent, Property, FeatureRequestPayload,
} from '../types/propertyApi';
import type { AdminUser, DeletionRequest } from '../types/admin';
import type { UserRole } from '../types/auth';

export async function searchAdminUsers(
  token: string,
  params: { q?: string; role?: string; skip?: number; limit?: number } = {}
): Promise<PaginatedResponse<AdminUser>> {
  const qs = new URLSearchParams();
  qs.set('skip', String(params.skip ?? 0));
  qs.set('limit', String(params.limit ?? 20));
  if (params.q) qs.set('q', params.q);
  if (params.role) qs.set('role', params.role);
  return fetchJson<PaginatedResponse<AdminUser>>(
    `${API_BASE_URL}/admin/users?${qs}`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
}

export async function searchStaffUsers(
  token: string,
  params: { q?: string; role?: string; skip?: number; limit?: number } = {}
): Promise<PaginatedResponse<AdminUser>> {
  const qs = new URLSearchParams();
  qs.set('skip', String(params.skip ?? 0));
  qs.set('limit', String(params.limit ?? 20));
  if (params.q) qs.set('q', params.q);
  if (params.role) qs.set('role', params.role);
  return fetchJson<PaginatedResponse<AdminUser>>(
    `${API_BASE_URL}/staff/users?${qs}`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
}

export async function searchStaffAgents(
  token: string,
  params: { q?: string; skip?: number; limit?: number } = {}
): Promise<PaginatedResponse<PublicAgent>> {
  const qs = new URLSearchParams();
  qs.set('skip', String(params.skip ?? 0));
  qs.set('limit', String(params.limit ?? 20));
  if (params.q) qs.set('q', params.q);
  return fetchJson<PaginatedResponse<PublicAgent>>(
    `${API_BASE_URL}/staff/agents?${qs}`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
}

export async function deleteAdminUser(
  token: string,
  userId: string
): Promise<{ message: string }> {
  return fetchJson<{ message: string }>(
    `${API_BASE_URL}/admin/users/${userId}`,
    { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } }
  );
}

export async function updateUserRoles(
  token: string,
  userId: string,
  roles: UserRole[]
): Promise<{ message: string; user_id: string; role: string; roles: string[] }> {
  return fetchJson<{ message: string; user_id: string; role: string; roles: string[] }>(
    `${API_BASE_URL}/admin/users/${userId}/roles`,
    {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ roles }),
    }
  );
}

/** @deprecated Use updateUserRoles. Kept as a thin wrapper during migration. */
export async function changeUserRole(
  token: string,
  userId: string,
  role: UserRole
): Promise<{ message: string; user_id: string; role: string }> {
  const res = await updateUserRoles(token, userId, [role]);
  return { message: res.message, user_id: res.user_id, role: res.role };
}

export async function patchUserStatus(
  token: string,
  userId: string,
  active: boolean
): Promise<{ message: string; user_id: string; is_active: boolean }> {
  return fetchJson<{ message: string; user_id: string; is_active: boolean }>(
    `${API_BASE_URL}/admin/users/${userId}/status`,
    {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ active }),
    }
  );
}

export async function promoteToAgent(
  token: string,
  userId: string,
  agentId: string
): Promise<{ message: string; user_id: string; agent_id: string; role: string }> {
  return fetchJson<{ message: string; user_id: string; agent_id: string; role: string }>(
    `${API_BASE_URL}/admin/promote-agent/${userId}`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ agent_id: agentId }),
    }
  );
}

export async function listDeletionRequests(
  token: string,
  params: { status?: string; skip?: number; limit?: number } = {}
): Promise<PaginatedResponse<DeletionRequest>> {
  const qs = new URLSearchParams();
  qs.set('skip', String(params.skip ?? 0));
  qs.set('limit', String(params.limit ?? 20));
  if (params.status) qs.set('status', params.status);
  return fetchJson<PaginatedResponse<DeletionRequest>>(
    `${API_BASE_URL}/admin/deletion-requests?${qs}`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
}

export async function submitDeletionRequest(
  token: string,
  targetUserId: string,
  reason: string
): Promise<DeletionRequest> {
  return fetchJson<DeletionRequest>(
    `${API_BASE_URL}/staff/deletion-requests`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ target_user_id: targetUserId, reason }),
    }
  );
}

export async function listStaffDeletionRequests(
  token: string,
  params: { skip?: number; limit?: number } = {}
): Promise<PaginatedResponse<DeletionRequest>> {
  const qs = new URLSearchParams();
  qs.set('skip', String(params.skip ?? 0));
  qs.set('limit', String(params.limit ?? 20));
  return fetchJson<PaginatedResponse<DeletionRequest>>(
    `${API_BASE_URL}/staff/deletion-requests?${qs}`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
}

export async function handleDeletionRequest(
  token: string,
  requestId: string,
  decision: { status: 'approved' | 'rejected'; review_note?: string }
): Promise<DeletionRequest> {
  return fetchJson<DeletionRequest>(
    `${API_BASE_URL}/admin/deletion-requests/${requestId}`,
    {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(decision),
    }
  );
}

// ===================== Featured listings (free editorial promotion) =====================

/** Currently-active featured promotions (admin panel). */
export async function listFeaturedProperties(
  token: string,
  params: { skip?: number; limit?: number } = {}
): Promise<PaginatedResponse<Property>> {
  const qs = new URLSearchParams();
  qs.set('skip', String(params.skip ?? 0));
  qs.set('limit', String(params.limit ?? 50));
  return fetchJson<PaginatedResponse<Property>>(
    `${API_BASE_URL}/admin/featured?${qs}`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
}

/** Feature or unfeature a listing, optionally for a fixed duration. */
export async function setPropertyFeatured(
  token: string,
  propertyId: string,
  payload: FeatureRequestPayload
): Promise<Property> {
  return fetchJson<Property>(
    `${API_BASE_URL}/admin/properties/${propertyId}/feature`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(payload),
    }
  );
}
