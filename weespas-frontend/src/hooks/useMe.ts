// useMe — single source of truth for the authenticated user across the app.
//
// Why a React Query layer on top of AuthContext:
// - AuthContext.user is set once on login and never refreshes; downstream
//   screens (ProfilePage, PreferencesPanel, NavBar) that want to display
//   fresh server state have to either re-fetch /auth/me themselves or
//   subscribe to a shared cache. We pick the cache.
// - `queryKey: ['auth', 'me']` is a single key — `useMutation` flows in
//   `useUpdateMe` write directly to it via `setQueryData`, so every
//   subscriber re-renders without a network round-trip.
// - `staleTime: 5 min` keeps tab-switches and remounts cheap. The AuthContext
//   localStorage cache still drives first paint; this hook only fires once
//   per stale window.
// - `placeholderData: prev` (via `keepPreviousData` semantics in v5) avoids
//   a loading flash on background revalidations.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchMe, updateMe, type UserUpdateRequest } from '../api/auth';
import { useAuth } from '../context/AuthContext';
import type { User } from '../types/auth';

export const ME_QUERY_KEY = ['auth', 'me'] as const;

export function useMe() {
  const { token, user: contextUser } = useAuth();

  return useQuery<User, Error>({
    queryKey: ME_QUERY_KEY,
    queryFn: () => fetchMe(token!),
    enabled: !!token,
    // AuthContext already restored the user from localStorage; surface it
    // immediately so consumers don't render a loading skeleton on mount.
    initialData: contextUser ?? undefined,
    staleTime: 5 * 60 * 1000,
    gcTime: 30 * 60 * 1000,
    retry: 1,
  });
}

export function useUpdateMe() {
  const { token } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<User, Error, UserUpdateRequest, { previous?: User }>({
    mutationFn: (patch) => updateMe(token!, patch),
    // Optimistic update: write the patched fields into the cache before the
    // network round-trip resolves so toggles feel instantaneous. Roll back
    // on error.
    onMutate: async (patch) => {
      await queryClient.cancelQueries({ queryKey: ME_QUERY_KEY });
      const previous = queryClient.getQueryData<User>(ME_QUERY_KEY);
      if (previous) {
        queryClient.setQueryData<User>(ME_QUERY_KEY, { ...previous, ...patch });
      }
      return { previous };
    },
    onError: (_err, _patch, context) => {
      if (context?.previous) {
        queryClient.setQueryData(ME_QUERY_KEY, context.previous);
      }
    },
    onSuccess: (fresh) => {
      // Server response wins (in case the backend mutated the value, e.g.
      // trimmed the name or normalized the avatar URL).
      queryClient.setQueryData(ME_QUERY_KEY, fresh);
      // Mirror to localStorage so AuthContext stays in sync across reloads.
      try {
        localStorage.setItem('weespas_user', JSON.stringify(fresh));
      } catch {
        // Quota / private-mode failures are non-fatal — React Query cache
        // still drives the live UI.
      }
    },
  });
}
