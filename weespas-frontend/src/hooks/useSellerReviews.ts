// Seller reviews hook — proof-of-purchase social proof (§8, catalogue-flavored per §8.4).
//
// A keyset infinite query over a seller's reviews (newest-first, opaque cursor from the server —
// same shape as useListingComments). The response's `summary` (aggregate rating + count) is
// carried on EVERY page and mirrored to a top-level `summary` here so the storefront header can
// render "★ 4.7 · 128 reviews" without waiting for the caller to flatten pages. `enabled` is
// bounded by (session, sellerId): opening/closing the storefront never fires a stray request.
//
// Compatible with §8.4's locked "shops stay a catalogue" split: this hook feeds the storefront's
// Reviews TAB and the header aggregate — it does NOT drive a live social-feed under the shop.
import {
  useInfiniteQuery,
  type InfiniteData,
} from '@tanstack/react-query';
import {
  getSellerReviews,
  type CommerceSession,
  type RatingSummary,
  type ReviewOut,
  type SellerReviewsPage,
} from '../api/commerce';

/** UNRATED_SUMMARY — the sentinel shown before the first page lands (or when the seller has none).
 *  Matches the server's zero-review shape (average = null, count = 0) so the header renders the
 *  same "unrated" pill in both cases (no false-precision "0.0 ★"). */
const UNRATED_SUMMARY: RatingSummary = { average: null, count: 0 };

export function useSellerReviews(
  session: CommerceSession | null,
  sellerId: string | null,
) {
  const query = useInfiniteQuery<
    SellerReviewsPage,
    Error,
    InfiniteData<SellerReviewsPage, string | null>,
    readonly unknown[],
    string | null
  >({
    // Keyed by (commerce base, sellerId): a different seller / a different commerce backend never
    // shares cache. Matches the useStorefront key shape so cache invalidations line up.
    queryKey: ['commerce', 'reviews', session?.commerce_url, sellerId],
    queryFn: ({ pageParam }) =>
      getSellerReviews(session!, sellerId!, { cursor: pageParam }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    initialPageParam: null,
    enabled: !!session && !!sellerId,
    // A review is stable data (append-only, no edit path — see the commerce Review model doc).
    // 60s stale mirrors useStorefront so the two hooks refetch together when the storefront tab
    // is reopened.
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    retry: 1,
  });

  // Flatten pages once; the server returns them newest-first so the concat order is already right.
  const items: ReviewOut[] = query.data?.pages.flatMap((p) => p.items) ?? [];
  // Every page carries the aggregate — use the FIRST page's summary as the authoritative header
  // number. Later pages can only add older reviews, so the summary is stable across pagination.
  // Before the first page lands, fall back to UNRATED_SUMMARY so the header can render at once.
  const summary: RatingSummary = query.data?.pages[0]?.summary ?? UNRATED_SUMMARY;

  return { ...query, items, summary };
}
