import { useMemo } from 'react';
import { useQueries } from '@tanstack/react-query';
import { searchAdminUsers, searchStaffUsers, searchStaffAgents } from '../api/admin';
import { fetchPublicAgents } from '../api/agents';
import { searchProperties } from '../api/properties';
import type { SearchResultItem } from '../types/admin';
import type { AdminUser } from '../types/admin';
import type { PublicAgent, Property } from '../types/propertyApi';
import type { UserRole } from '../types/auth';

const SEARCH_LIMIT = 20;
const STALE_TIME = 1000 * 60 * 2;

function userToResult(u: AdminUser): SearchResultItem {
  return {
    id: u.id,
    category: 'user',
    name: u.name,
    subtitle: u.email,
    role: u.role,
    roles: u.roles,
    avatar: u.avatar,
    is_active: u.is_active,
    agent_id: u.agent_id,
    raw: u,
  };
}

function agentToResult(a: PublicAgent): SearchResultItem {
  return {
    id: a.id,
    category: 'agent',
    name: a.agent_name,
    subtitle: `${a.property_count} properties`,
    role: 'agent',
    avatar: a.agent_profile_picture,
    user_id: a.user_id,
    raw: a,
  };
}

function propertyToResult(p: Property): SearchResultItem {
  return {
    id: p.id,
    category: 'property',
    name: p.title,
    subtitle: p.address?.location_name ?? p.location_name ?? '',
    raw: p,
  };
}

export function useUnifiedSearch(
  token: string | null,
  query: string,
  userRole?: UserRole,
) {
  const enabled = Boolean(token) && query.trim().length >= 2;
  const canViewUsers = userRole === 'admin' || userRole === 'staff';
  const canViewStaffAgents = userRole === 'admin' || userRole === 'staff';

  const results = useQueries({
    queries: [
      {
        queryKey: ['unifiedSearch', 'users', query, userRole],
        queryFn: () =>
          userRole === 'admin'
            ? searchAdminUsers(token!, { q: query, limit: SEARCH_LIMIT })
            : searchStaffUsers(token!, { q: query, limit: SEARCH_LIMIT }),
        enabled: enabled && canViewUsers,
        staleTime: STALE_TIME,
        retry: 1,
      },
      {
        queryKey: ['unifiedSearch', 'agents', query, canViewStaffAgents],
        queryFn: () =>
          canViewStaffAgents
            ? searchStaffAgents(token!, { q: query, limit: SEARCH_LIMIT })
            : fetchPublicAgents({ q: query, limit: SEARCH_LIMIT }),
        enabled,
        staleTime: STALE_TIME,
        retry: 1,
      },
      {
        queryKey: ['unifiedSearch', 'properties', query],
        queryFn: () => searchProperties(query, 0, SEARCH_LIMIT),
        enabled,
        staleTime: STALE_TIME,
        retry: 1,
      },
    ],
  });

  const [usersQuery, agentsQuery, propertiesQuery] = results;

  const isLoading = results.some((r) => r.isLoading);
  const isError = results.every((r) => r.isError);

  const items = useMemo<SearchResultItem[]>(() => {
    const users = (usersQuery.data?.items ?? []).map(userToResult);
    const agents = (agentsQuery.data?.items ?? []).map(agentToResult);
    const properties = (propertiesQuery.data?.items ?? []).map(propertyToResult);
    return [...users, ...agents, ...properties];
  }, [usersQuery.data, agentsQuery.data, propertiesQuery.data]);

  const totalCount = useMemo(
    () =>
      (usersQuery.data?.total ?? 0) +
      (agentsQuery.data?.total ?? 0) +
      (propertiesQuery.data?.total ?? 0),
    [usersQuery.data, agentsQuery.data, propertiesQuery.data],
  );

  return { items, isLoading, isError, totalCount };
}
