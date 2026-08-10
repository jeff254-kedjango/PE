// The server-authoritative Boost tier catalogue (reach radius, free cap, default window, nominal
// price) for the current business day. The BoostChooser reads reach km / order / price from HERE
// instead of hard-coding them, so the FE copy and the backend config can never drift.
//
// Long staleTime: the catalogue is derived from server config (not per-request state) and changes
// only on a redeploy, so there's no value in refetching it while the chooser is open. It is a
// separate query key from the per-day allowances (which DO change as chances are spent).
import { useQuery } from '@tanstack/react-query';
import { getBoostTiers, type CommerceSession, type BoostTiersOut } from '../api/commerce';

export const BOOST_TIERS_KEY = ['commerce', 'boosts', 'tiers'] as const;

export function useBoostTiers(session: CommerceSession | null) {
  return useQuery<BoostTiersOut, Error>({
    queryKey: [...BOOST_TIERS_KEY, session?.commerce_url],
    queryFn: () => getBoostTiers(session!),
    enabled: !!session,
    staleTime: 60 * 60 * 1000, // 1 h — config-derived, effectively static within a session
    retry: 1,
  });
}
