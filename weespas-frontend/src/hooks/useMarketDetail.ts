// useMarketDetail — reads /weesstock/markets/{seller_id} for the investor deep-dive page.
//
// The query key includes the seller id, so navigating between detail pages re-fetches the
// right seller instead of serving the previous one. Cadence matches useMarkets (5 min):
// the profile is the same slow-moving aggregate the seller's own card reads.
import { useQuery } from '@tanstack/react-query';
import { getMarketDetail, type CommerceSession, type MarketDetailOut } from '../api/commerce';

export function useMarketDetail(session: CommerceSession | null, sellerId: string | undefined) {
  return useQuery<MarketDetailOut, Error>({
    queryKey: ['commerce', 'weesstock', 'market-detail', session?.commerce_url, sellerId],
    queryFn: () => getMarketDetail(session!, sellerId!),
    enabled: !!session && !!sellerId,
    staleTime: 300_000,
    retry: 1,
  });
}
