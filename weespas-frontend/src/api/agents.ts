import { fetchJson, API_BASE_URL } from './config';
import { PaginatedResponse, Property, PublicAgent } from '../types/propertyApi';
import { AgentStats } from '../types/stats';

// ── Public (no auth) ──────────────────────────────────────────────

export async function fetchPublicAgents(
  params: { skip?: number; limit?: number; q?: string } = {}
): Promise<PaginatedResponse<PublicAgent>> {
  const qs = new URLSearchParams();
  qs.set('skip', String(params.skip ?? 0));
  qs.set('limit', String(params.limit ?? 10));
  if (params.q) qs.set('q', params.q);
  return fetchJson<PaginatedResponse<PublicAgent>>(
    `${API_BASE_URL}/agents/public?${qs}`
  );
}

export async function fetchPublicAgentById(
  agentId: string
): Promise<PublicAgent> {
  return fetchJson<PublicAgent>(
    `${API_BASE_URL}/agents/public/${agentId}`
  );
}

export async function fetchPublicAgentProperties(
  agentId: string,
  params: { skip?: number; limit?: number } = {}
): Promise<PaginatedResponse<Property>> {
  const skip = params.skip ?? 0;
  const limit = params.limit ?? 10;
  return fetchJson<PaginatedResponse<Property>>(
    `${API_BASE_URL}/agents/public/${agentId}/properties?skip=${skip}&limit=${limit}`
  );
}

// ── Authenticated ─────────────────────────────────────────────────

export async function fetchAgentStats(
  token: string,
  scope: 'mine' | 'global' = 'mine'
): Promise<AgentStats> {
  return fetchJson<AgentStats>(
    `${API_BASE_URL}/agents/me/stats?scope=${scope}`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
}

export async function fetchAgentProperties(
  token: string,
  params: { skip?: number; limit?: number } = {}
): Promise<PaginatedResponse<Property>> {
  const skip = params.skip ?? 0;
  const limit = params.limit ?? 10;
  return fetchJson<PaginatedResponse<Property>>(
    `${API_BASE_URL}/agents/me/properties?skip=${skip}&limit=${limit}`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
}
