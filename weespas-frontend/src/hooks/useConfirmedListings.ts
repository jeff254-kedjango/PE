// Ground-confirmed listings hook — feeds the green shield on listing cards.
//
// Takes the listing ids currently on screen and asks the backend, in ONE batched
// call, which of them map to a building with a recorded on-the-ground assessment.
// Returns a Set for O(1) per-card lookup. Keyed by the sorted id list so paging /
// scope-toggle re-queries only when the visible set actually changes.
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import { getConfirmedListings } from '../api/insar';

export function useConfirmedListings(listingIds: string[]): Set<string> {
  const { token, isAuthenticated } = useAuth();

  // Stable, order-independent key + request payload (so [a,b] and [b,a] share a cache
  // entry and don't thrash). Empty list ⇒ the query is disabled (no request).
  const sortedIds = useMemo(() => [...listingIds].sort(), [listingIds]);

  const { data } = useQuery<Record<string, boolean>, Error>({
    queryKey: ['insar', 'confirmed-listings', sortedIds],
    queryFn: () => getConfirmedListings(token!, sortedIds),
    enabled: !!token && isAuthenticated && sortedIds.length > 0,
    staleTime: 5 * 60_000,
    retry: 1,
  });

  return useMemo(() => {
    const s = new Set<string>();
    if (data) for (const [id, ok] of Object.entries(data)) if (ok) s.add(id);
    return s;
  }, [data]);
}
