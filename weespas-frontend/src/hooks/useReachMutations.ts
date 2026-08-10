// FE-2b "reach & respond" mutations: promote/clear a listing, open/revoke a Boost, mark an
// inquiry read. Same discipline as useSellerMutations — UI-agnostic (no toasts; components surface
// success/error via useToast), all guard with session!.
//
// Invalidation targets per mutation:
//   * promote / clear  → MY_STOREFRONT_KEY (the dashboard row's promo state) + the feed prefix
//                        (a "selling now" window changes the buyer feed).
//   * boost / revoke   → BOOST_ALLOWANCES_KEY (the remaining count must visibly decrement) + the
//                        feed prefix (a sponsored slot changes the feed).
//   * mark read        → MY_INQUIRIES_KEY (the item's unread styling + the unread badge).
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  promoteListing,
  clearPromotion,
  launchFlashSale,
  clearFlashSale,
  createBoost,
  revokeBoost,
  markInquiryRead,
  type CommerceSession,
  type PromoteRequest,
  type FlashSaleRequest,
  type BoostRequest,
  type BoostGrantOut,
  type ListingOut,
} from '../api/commerce';
import { MY_STOREFRONT_KEY } from './useMyStorefront';
import { BOOST_ALLOWANCES_KEY } from './useBoostAllowances';
import { MY_INQUIRIES_KEY } from './useMyInquiries';
import { FLASH_SALES_KEY_PREFIX } from './useFlashSales';

const FEED_KEY_PREFIX = ['commerce', 'feed'] as const;

export function usePromoteListing(session: CommerceSession | null, listingId: string) {
  const qc = useQueryClient();
  return useMutation<ListingOut, Error, PromoteRequest>({
    mutationFn: (body) => promoteListing(session!, listingId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: MY_STOREFRONT_KEY });
      qc.invalidateQueries({ queryKey: FEED_KEY_PREFIX });
    },
  });
}

export function useClearPromotion(session: CommerceSession | null, listingId: string) {
  const qc = useQueryClient();
  return useMutation<ListingOut, Error, void>({
    mutationFn: () => clearPromotion(session!, listingId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: MY_STOREFRONT_KEY });
      qc.invalidateQueries({ queryKey: FEED_KEY_PREFIX });
    },
  });
}

export function useLaunchFlashSale(session: CommerceSession | null, listingId: string) {
  const qc = useQueryClient();
  return useMutation<ListingOut, Error, FlashSaleRequest>({
    mutationFn: (body) => launchFlashSale(session!, listingId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: MY_STOREFRONT_KEY });     // the dashboard row's flash state
      qc.invalidateQueries({ queryKey: FLASH_SALES_KEY_PREFIX }); // the nationwide buyer slate
    },
  });
}

export function useClearFlashSale(session: CommerceSession | null, listingId: string) {
  const qc = useQueryClient();
  return useMutation<ListingOut, Error, void>({
    mutationFn: () => clearFlashSale(session!, listingId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: MY_STOREFRONT_KEY });
      qc.invalidateQueries({ queryKey: FLASH_SALES_KEY_PREFIX });
    },
  });
}

export function useCreateBoost(session: CommerceSession | null) {
  const qc = useQueryClient();
  return useMutation<BoostGrantOut, Error, BoostRequest>({
    mutationFn: (body) => createBoost(session!, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: BOOST_ALLOWANCES_KEY });
      qc.invalidateQueries({ queryKey: FEED_KEY_PREFIX });
    },
  });
}

export function useRevokeBoost(session: CommerceSession | null) {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (grantId) => revokeBoost(session!, grantId),
    onSuccess: () => {
      // Revoke does NOT refund the chance, so allowances don't change — but the feed does (the
      // sponsored slot is gone). Still refetch allowances cheaply to stay consistent with the day.
      qc.invalidateQueries({ queryKey: BOOST_ALLOWANCES_KEY });
      qc.invalidateQueries({ queryKey: FEED_KEY_PREFIX });
    },
  });
}

export function useMarkInquiryRead(session: CommerceSession | null) {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (inquiryId) => markInquiryRead(session!, inquiryId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: MY_INQUIRIES_KEY });
    },
  });
}
