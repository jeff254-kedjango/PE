// Candidate footprints + confirm hook for the "bad pin" tap-to-confirm flow.
//
// When a listing's pin lands in a cluster of footprints the backend can't safely
// auto-pick (coverage 'needs_confirmation'), the listing OWNER taps the right building.
// `useListingCandidates` fetches the plausible candidates (owner-only endpoint);
// `useConfirmListingBuilding` persists the choice and invalidates the risk badge so the
// RiskPill flips from provisional to the confirmed tier.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import {
  getListingCandidates,
  confirmListingBuilding,
  type ListingCandidates,
  type ListingRisk,
} from '../api/insar';

/**
 * The candidate footprints a clustered pin could be. Owner/agent only (the endpoint is
 * auth-gated and ownership-checked), so the query is disabled without a token or when the
 * caller hasn't opted in (`enabled`). Candidates are a slow-moving signal — cached, no
 * refetch on focus.
 */
export function useListingCandidates(listingId: string | undefined, enabled = true) {
  const { token } = useAuth();
  return useQuery<ListingCandidates, Error>({
    queryKey: ['insarCandidates', listingId],
    queryFn: () => getListingCandidates(token!, listingId as string),
    enabled: !!token && !!listingId && enabled,
    staleTime: 1000 * 60 * 30,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}

/**
 * Persist the owner's building choice. On success, invalidate the listing's risk badge
 * (so it re-reads the now-confirmed tier) and its candidate set (now resolved).
 */
export function useConfirmListingBuilding(listingId: string) {
  const { token } = useAuth();
  const qc = useQueryClient();
  return useMutation<ListingRisk, Error, number>({
    mutationFn: (insarBuildingId: number) =>
      confirmListingBuilding(token!, listingId, insarBuildingId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['listingRisk', listingId] });
      qc.invalidateQueries({ queryKey: ['insarCandidates', listingId] });
    },
  });
}
