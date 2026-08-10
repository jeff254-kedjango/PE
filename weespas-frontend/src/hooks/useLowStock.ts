// useLowStock — reads /sellers/me/low-stock for the LowStockCard (§8 Chunk E2).
//
// Polling cadence matches the ViewingCard rhythm: every 30s while the tab is visible. The
// user is likely to be adjusting stock manually (via StockControl on the dashboard) while
// this card is on screen, so we ALSO expose the query key on the module so the mutation
// path can invalidate it explicitly after a stock write (see useSellerMutations.ts).
import { useQuery } from '@tanstack/react-query';
import { getMyLowStock, type CommerceSession, type LowStockOut } from '../api/commerce';

/** Default poll cadence — same as ViewingCard's live-count. A background tab stops polling. */
export const LOW_STOCK_POLL_MS = 30_000;
/** Default shop-wide floor. Matches the backend default; making it explicit here means a caller
 *  who wants a different threshold passes it deliberately. */
export const LOW_STOCK_DEFAULT_FLOOR = 5;

export const LOW_STOCK_KEY = ['commerce', 'seller', 'low-stock'] as const;

export function useLowStock(
  session: CommerceSession | null,
  floor: number = LOW_STOCK_DEFAULT_FLOOR,
) {
  return useQuery<LowStockOut, Error>({
    // Floor participates in the key: a threshold change reissues the query, no stale render.
    queryKey: [...LOW_STOCK_KEY, session?.commerce_url, floor],
    queryFn: () => getMyLowStock(session!, { floor }),
    enabled: !!session,
    staleTime: LOW_STOCK_POLL_MS,
    refetchInterval: LOW_STOCK_POLL_MS,
    refetchIntervalInBackground: false,
    retry: 1,
  });
}
