// useSavedSearches — React Query hook for Phase 3.
//
// Performance:
// - Server caps the list at 25; queryKey is per-user (via the auth token
//   in the queryFn), staleTime 1 min so panel re-opens are instant.
// - Apply ("touch") is a fire-and-forget mutation; we don't block the
//   navigation on the round-trip — last_used_at is best-effort.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import {
  createSavedSearch,
  deleteSavedSearch,
  listSavedSearches,
  updateSavedSearch,
  type SavedSearch,
} from '../api/savedSearches';

export const SAVED_SEARCHES_KEY = ['me', 'saved-searches'] as const;

export function useSavedSearches() {
  const { token } = useAuth();
  return useQuery<SavedSearch[], Error>({
    queryKey: SAVED_SEARCHES_KEY,
    queryFn: () => listSavedSearches(token!),
    enabled: !!token,
    staleTime: 60_000,
  });
}

export function useCreateSavedSearch() {
  const { token } = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; filters: Record<string, unknown> }) =>
      createSavedSearch(token!, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: SAVED_SEARCHES_KEY }),
  });
}

export function useDeleteSavedSearch() {
  const { token } = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteSavedSearch(token!, id),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: SAVED_SEARCHES_KEY });
      const prev = qc.getQueryData<SavedSearch[]>(SAVED_SEARCHES_KEY) ?? [];
      qc.setQueryData<SavedSearch[]>(
        SAVED_SEARCHES_KEY,
        prev.filter((s) => s.id !== id),
      );
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(SAVED_SEARCHES_KEY, ctx.prev);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: SAVED_SEARCHES_KEY }),
  });
}

export function useTouchSavedSearch() {
  // Fire-and-forget — never blocks navigation. The optimistic local
  // re-ordering would cost more than it saves; the next list fetch will
  // surface the new last_used_at order.
  const { token } = useAuth();
  return useMutation({
    mutationFn: (id: string) => updateSavedSearch(token!, id, { touch: true }),
  });
}
