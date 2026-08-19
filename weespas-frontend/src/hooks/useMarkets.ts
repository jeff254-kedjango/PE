// useMarkets — reads /weesstock/markets for the investor Markets page (§WeesStock F4).
//
// Cadence mirrors the credit profile (5 min): a market entry only moves when a receipt
// settles, a review lands, or a day of tenure passes — polling it at feed speed would be a
// pile of pointless aggregate reads over the heaviest query family in the service.
import { useQuery } from '@tanstack/react-query';
import { getMarkets, type CommerceSession, type MarketListOut } from '../api/commerce';

/** 5 minutes — same slow-moving cadence as the seller's own credit profile. */
export const MARKETS_POLL_MS = 300_000;

export const MARKETS_KEY = ['commerce', 'weesstock', 'markets'] as const;

export function useMarkets(session: CommerceSession | null) {
  return useQuery<MarketListOut, Error>({
    queryKey: [...MARKETS_KEY, session?.commerce_url],
    queryFn: () => getMarkets(session!),
    enabled: !!session,
    staleTime: MARKETS_POLL_MS,
    refetchInterval: MARKETS_POLL_MS,
    refetchIntervalInBackground: false,
    retry: 1,
  });
}
