// The signed-in seller's OWN storefront — the seller-console dashboard's source of truth.
//
// Unlike useStorefront (a buyer's view of any seller, in-stock only), this returns the caller's
// own shops with ALL listings (incl. out-of-stock) + their rating. Keyed by the commerce base so
// it caches per session; the seller mutations (useSellerMutations) invalidate this key on success.
import { useQuery } from '@tanstack/react-query';
import { getMyStorefront, type CommerceSession, type StorefrontOut } from '../api/commerce';

export const MY_STOREFRONT_KEY = ['commerce', 'shops', 'mine'] as const;

export function useMyStorefront(session: CommerceSession | null) {
  return useQuery<StorefrontOut, Error>({
    queryKey: [...MY_STOREFRONT_KEY, session?.commerce_url],
    queryFn: () => getMyStorefront(session!),
    enabled: !!session,
    staleTime: 30_000,
    retry: 1,
  });
}
