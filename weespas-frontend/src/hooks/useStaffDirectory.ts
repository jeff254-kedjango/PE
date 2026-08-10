import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { searchStaffUsers, searchStaffAgents } from '../api/admin';
import type { AdminUser } from '../types/admin';
import type { PublicAgent, PaginatedResponse } from '../types/propertyApi';

export type DirectoryMode = 'users' | 'agents' | 'staff';

export interface DirectoryItem {
  id: string;
  name: string;
  avatar?: string | null;
  email?: string;
  phone?: string;
  roles: string[];
  last_seen_at?: string | null;
  is_online: boolean;
  agent_id?: string | null;
  user_id?: string | null;
  subtitle: string;
  source: 'user' | 'agent';
}

export const DIRECTORY_PAGE_SIZE = 10;
const REFETCH_INTERVAL = 30_000;

function userToItem(u: AdminUser): DirectoryItem {
  return {
    id: u.id,
    name: u.name,
    avatar: u.avatar,
    email: u.email,
    phone: u.phone,
    roles: u.roles ?? [u.role],
    last_seen_at: u.last_seen_at ?? null,
    is_online: Boolean(u.is_online),
    agent_id: u.agent_id ?? null,
    subtitle: [u.email, u.phone].filter(Boolean).join(' · '),
    source: 'user',
  };
}

function agentToItem(a: PublicAgent): DirectoryItem {
  const propLabel = `${a.property_count} ${a.property_count === 1 ? 'property' : 'properties'}`;
  return {
    id: a.id,
    name: a.agent_name,
    avatar: a.agent_profile_picture ?? null,
    email: a.email,
    phone: a.agent_phone_number,
    roles: a.roles ?? ['agent'],
    last_seen_at: a.last_seen_at ?? null,
    is_online: Boolean(a.is_online),
    user_id: a.user_id ?? null,
    subtitle: [a.agent_phone_number, propLabel].filter(Boolean).join(' · '),
    source: 'agent',
  };
}

export function useStaffDirectory(
  token: string | null,
  mode: DirectoryMode,
  query: string,
  page: number,
) {
  const enabled = Boolean(token);
  const q = query.trim() || undefined;
  const skip = Math.max(0, page) * DIRECTORY_PAGE_SIZE;

  const usersQuery = useQuery<PaginatedResponse<AdminUser | PublicAgent>>({
    queryKey: ['staffDirectory', mode, q, skip],
    queryFn: async () => {
      if (mode === 'agents') {
        return (await searchStaffAgents(token!, { q, limit: DIRECTORY_PAGE_SIZE, skip })) as PaginatedResponse<AdminUser | PublicAgent>;
      }
      const role = mode === 'staff' ? 'staff' : 'user';
      return (await searchStaffUsers(token!, { q, role, limit: DIRECTORY_PAGE_SIZE, skip })) as PaginatedResponse<AdminUser | PublicAgent>;
    },
    enabled,
    refetchInterval: REFETCH_INTERVAL,
    staleTime: 15_000,
    retry: 1,
    placeholderData: (prev) => prev,
  });

  const items = useMemo<DirectoryItem[]>(() => {
    const raw = usersQuery.data?.items ?? [];
    if (mode === 'agents') return (raw as PublicAgent[]).map(agentToItem);
    return (raw as AdminUser[]).map(userToItem);
  }, [usersQuery.data, mode]);

  return {
    items,
    total: usersQuery.data?.total ?? 0,
    isLoading: usersQuery.isLoading,
    isFetching: usersQuery.isFetching,
    error: usersQuery.error,
    refetch: usersQuery.refetch,
  };
}
