// Public storefront hook — a seller's buyer-visible profile (in-stock listings + rating).
//
// Read-only, keyed by (commerce base, sellerId). Disabled until both a session and a sellerId
// exist, so opening/closing the panel doesn't fire stray requests.
//
// §8 storefront: two entry points, ONE component (Storefront.tsx). A buyer arrives via either a
// sellerId (legacy /shop/<sellerId> URL, the trade-sheet from ShopHoverCard) or a handle (the
// shareable /shop/<handle> URL). Both hooks return the SAME PublicStorefront shape — the caller
// picks whichever it has at hand; the other is disabled. React Query keys stay distinct so the
// cache doesn't collide between the two lookup paths.
import { useQuery } from '@tanstack/react-query';
import {
  getPublicStorefront, getPublicStorefrontByHandle,
  type CommerceSession, type PublicStorefront,
} from '../api/commerce';

export function useStorefront(session: CommerceSession | null, sellerId: string | null) {
  return useQuery<PublicStorefront, Error>({
    queryKey: ['commerce', 'storefront', session?.commerce_url, sellerId],
    queryFn: () => getPublicStorefront(session!, sellerId!),
    enabled: !!session && !!sellerId,
    staleTime: 60_000,
    retry: 1,
  });
}

/** Handle-keyed sibling of {@link useStorefront} — same DTO, distinct cache key so a page mount
 *  by handle and a sheet mount by sellerId for the same shop don't collide. The handle is
 *  case-insensitive server-side; we lower it in the key so `/shop/Mama-Mboga` and `/shop/mama-mboga`
 *  share a cache entry. */
export function useStorefrontByHandle(session: CommerceSession | null, handle: string | null) {
  const key = handle ? handle.trim().toLowerCase() : null;
  return useQuery<PublicStorefront, Error>({
    queryKey: ['commerce', 'storefront', 'byHandle', session?.commerce_url, key],
    queryFn: () => getPublicStorefrontByHandle(session!, key!),
    enabled: !!session && !!key,
    staleTime: 60_000,
    retry: 1,
  });
}
