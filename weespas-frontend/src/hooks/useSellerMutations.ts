// Seller write mutations for the console (FE-2a): create shop, create listing, adjust stock.
//
// Each invalidates TWO caches on success:
//   * ['commerce','shops','mine'] — the dashboard re-reads the seller's own storefront.
//   * ['commerce','feed']         — by PREFIX (partial match): the buyer feed keys carry
//                                   lat/lng/radius/kind, so a new/updated in-stock listing only
//                                   refreshes if we invalidate the whole feed family, not one key.
// Hooks stay UI-agnostic (no toasts here) — components surface success/error via useToast, matching
// useEngagement. All guard with session! (the page mounts these only once a session exists).
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  createShop,
  createListing,
  createPost,
  adjustStock,
  postBulkStockCsv,
  updateListing,
  deleteListing,
  claimShopHandle,
  type BulkStockOut,
  type CommerceSession,
  type ShopCreate,
  type ShopOut,
  type ListingCreate,
  type ListingOut,
  type ListingUpdate,
  type PostCreate,
  type StockAdjust,
} from '../api/commerce';
import { LOW_STOCK_KEY } from './useLowStock';
import { MY_STOREFRONT_KEY } from './useMyStorefront';

const FEED_KEY_PREFIX = ['commerce', 'feed'] as const;

function useInvalidateSellerViews() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: MY_STOREFRONT_KEY });   // prefix match — any commerce_url
    qc.invalidateQueries({ queryKey: FEED_KEY_PREFIX });     // prefix match — any lat/lng/radius/kind
  };
}

export function useCreateShop(session: CommerceSession | null) {
  const invalidate = useInvalidateSellerViews();
  return useMutation<ShopOut, Error, ShopCreate>({
    mutationFn: (body) => createShop(session!, body),
    onSuccess: invalidate,
  });
}

/** Claim a handle (§8 shareable URL slug) for one of the caller's shops. ONE-SHOT: once set,
 *  permanent. Invalidates the seller's own storefront (so the newly-set handle shows on the
 *  dashboard) — the buyer feed key isn't affected (handles aren't feed-ranked). Errors from the
 *  server carry the reason slug in `error.message`; the caller maps that to inline copy. */
export function useClaimShopHandle(session: CommerceSession | null, shopId: string) {
  const qc = useQueryClient();
  return useMutation<ShopOut, Error, string>({
    mutationFn: (handle) => claimShopHandle(session!, shopId, handle),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: MY_STOREFRONT_KEY });
    },
  });
}

export function useCreateListing(session: CommerceSession | null, shopId: string) {
  const invalidate = useInvalidateSellerViews();
  return useMutation<ListingOut, Error, ListingCreate>({
    mutationFn: (body) => createListing(session!, shopId, body),
    onSuccess: invalidate,
  });
}

export function useCreatePost(session: CommerceSession | null) {
  const invalidate = useInvalidateSellerViews();
  return useMutation<ListingOut, Error, PostCreate>({
    mutationFn: (body) => createPost(session!, body),
    onSuccess: invalidate,  // feed prefix (post appears) + shops/mine (personal shop may be new)
  });
}

export function useAdjustStock(session: CommerceSession | null, listingId: string) {
  const invalidate = useInvalidateSellerViews();
  return useMutation<ListingOut, Error, StockAdjust>({
    mutationFn: (body) => adjustStock(session!, listingId, body),
    onSuccess: invalidate,
  });
}

export function useUpdateListing(session: CommerceSession | null, listingId: string) {
  const invalidate = useInvalidateSellerViews();
  return useMutation<ListingOut, Error, ListingUpdate>({
    mutationFn: (body) => updateListing(session!, listingId, body),
    onSuccess: invalidate,  // dashboard re-reads + feed prefix (an edited in-stock listing refreshes)
  });
}

export function useDeleteListing(session: CommerceSession | null, listingId: string) {
  const invalidate = useInvalidateSellerViews();
  return useMutation<void, Error, void>({
    mutationFn: () => deleteListing(session!, listingId),
    onSuccess: invalidate,  // soft-deleted → drops from both the storefront and the buyer feed
  });
}

/** §8 Chunk E3 — bulk CSV stock upload. All-or-nothing on parse; unowned ids skipped. On
 *  success, invalidate the seller storefront (rows re-render with fresh stock), the feed
 *  (visibility changes on newly-out-of-stock listings), AND the low-stock card (a bulk
 *  restock is precisely the moment its list should shrink). */
export function useBulkStockCsv(session: CommerceSession | null) {
  const qc = useQueryClient();
  const invalidate = useInvalidateSellerViews();
  return useMutation<BulkStockOut, Error, string>({
    mutationFn: (csv) => postBulkStockCsv(session!, csv),
    onSuccess: () => {
      invalidate();
      qc.invalidateQueries({ queryKey: LOW_STOCK_KEY });
    },
  });
}
