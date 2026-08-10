// The signed-in seller's remaining free Boost chances per tier for the current business day.
//
// Short staleTime: the counts change as the seller (or the midnight reset) spends chances, and the
// BoostChooser disables a tier the moment its remaining hits 0 — so a stale count would let a user
// click a spent tier and eat a 429. useCreateBoost/useRevokeBoost invalidate this key on success
// so the displayed count visibly decrements right after a boost.
import { useQuery } from '@tanstack/react-query';
import { getBoostAllowances, type CommerceSession, type BoostAllowancesOut } from '../api/commerce';

export const BOOST_ALLOWANCES_KEY = ['commerce', 'boosts', 'allowances'] as const;

export function useBoostAllowances(session: CommerceSession | null) {
  return useQuery<BoostAllowancesOut, Error>({
    queryKey: [...BOOST_ALLOWANCES_KEY, session?.commerce_url],
    queryFn: () => getBoostAllowances(session!),
    enabled: !!session,
    staleTime: 15_000,
    retry: 1,
  });
}
