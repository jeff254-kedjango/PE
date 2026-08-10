// useFlashSales — fetches the nationwide Flash Sales slate (§8 "crazy offer" grid under Quick Buys).
//
// Unlike Quick Buys this is NOT personal and NOT location-scoped — every buyer sees the same
// platform-wide slate, ranked by craziness. lat/lng are optional and only add a display-only
// distance. A SHORT staleTime because flash windows turn over within the hour: a stale slate would
// show an offer that has already expired, so we re-poll more eagerly than the discovery grid.
import { useQuery } from '@tanstack/react-query';
import { getFlashSales, type CommerceSession, type FlashSalesResponse } from '../api/commerce';

const FLASH_SALES_STALE_MS = 30_000;

// Exported so the seller launch/clear mutations can invalidate the buyer slate.
export const FLASH_SALES_KEY_PREFIX = ['commerce', 'flash-sales'] as const;

interface UseFlashSalesArgs {
  session: CommerceSession | null;
  lat: number | null;
  lng: number | null;
}

export interface UseFlashSales {
  data: FlashSalesResponse | null;
  isLoading: boolean;
  isError: boolean;
}

export function useFlashSales({ session, lat, lng }: UseFlashSalesArgs): UseFlashSales {
  const { data, isLoading, isError } = useQuery<FlashSalesResponse, Error>({
    queryKey: [...FLASH_SALES_KEY_PREFIX, session?.commerce_url ?? 'none', lat, lng],
    queryFn: () => getFlashSales(session!, lat, lng),
    enabled: !!session,
    staleTime: FLASH_SALES_STALE_MS,
    retry: 1,
  });

  return { data: data ?? null, isLoading, isError };
}
