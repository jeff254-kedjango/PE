import { useQuery } from '@tanstack/react-query';
import { getListingRisk, type ListingRisk } from '../api/insar';

/**
 * A listing's InSAR coverage/risk badge (work_flow.md §9.3 Option B).
 *
 * Risk changes only on the ~12-day InSAR refresh cycle, so this is cached long and
 * never refetched on focus — the badge is a slow-moving signal, not live telemetry.
 * `enabled` lets the caller skip the request when there's no listing id.
 */
export function useListingRisk(listingId: string | undefined) {
  return useQuery<ListingRisk, Error>({
    queryKey: ['listingRisk', listingId],
    queryFn: () => getListingRisk(listingId as string),
    enabled: !!listingId,
    staleTime: 1000 * 60 * 30,   // 30 min — risk only moves on the ~12-day rebuild
    gcTime: 1000 * 60 * 60,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}
