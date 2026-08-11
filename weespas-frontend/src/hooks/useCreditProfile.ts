// useCreditProfile — reads /sellers/me/credit-profile for the WeesStock card (§WeesStock F2).
//
// Cadence is deliberately much slower than useLowStock's 30s. Stock moves every time the seller
// touches the POS; a credit profile only moves when a receipt settles, a review lands, or a day
// of tenure passes. Polling it at stock speed would be ~120 pointless aggregate queries an hour
// per open tab, over the heaviest read in the seller surface (it scans 90 days of receipts).
//
// The key is exported so the settlement path can invalidate explicitly — a seller who just
// completed the sale that clears the 10-order gate should see the score appear, not wait out
// the interval.
import { useQuery } from '@tanstack/react-query';
import { getMyCreditProfile, type CommerceSession, type CreditProfileOut } from '../api/commerce';

/** 5 minutes — matches the RankingCard rhythm, the other slow-moving intelligence card. */
export const CREDIT_PROFILE_POLL_MS = 300_000;

export const CREDIT_PROFILE_KEY = ['commerce', 'seller', 'credit-profile'] as const;

export function useCreditProfile(session: CommerceSession | null) {
  return useQuery<CreditProfileOut, Error>({
    queryKey: [...CREDIT_PROFILE_KEY, session?.commerce_url],
    queryFn: () => getMyCreditProfile(session!),
    enabled: !!session,
    staleTime: CREDIT_PROFILE_POLL_MS,
    refetchInterval: CREDIT_PROFILE_POLL_MS,
    refetchIntervalInBackground: false,
    retry: 1,
  });
}
