// useCommerceVideoShorts — the single source of truth for the Trade "Videos" surface.
//
// Adapts the commerce proximity feed (Videos lane) into the shape the REUSED real-estate shorts UI
// renders (ShortsShelf + VerticalVideoFeed), and owns the §8 SAVE state for those shorts. Extracted
// from ShopVideoStrip so BOTH consumers — the right-rail poster shelf AND the full-screen vertical
// overlay (opened from the middle-column Videos toggle) — share one fetch, one adapter, and one
// optimistic save set. React Query dedupes the kind:'videos' request by queryKey, so a single
// network call backs every consumer no matter how many mount this hook (in practice: one, on the page).
//
// Save state is optimistic + reconciled to the server's idempotent toggle, with rollback on failure.
// Engagement never enters ranking — this is display-only (mirrors the backend contract).
import { useCallback, useMemo, useRef, useState } from 'react';
import { useCommerceFeed } from './useCommerceFeed';
import { isVideoUrl } from '../utils/media';
import { formatPrice } from '../utils/format';
import { toggleSave, type CommerceSession, type FeedItem } from '../api/commerce';
import type { PropertyShort } from '../api/shorts';

interface UseCommerceVideoShortsArgs {
  session: CommerceSession | null;
  lat: number;
  lng: number;
}

export interface CommerceVideoShorts {
  /** One PropertyShort per nearby feed item that actually carries a video URL. */
  shorts: PropertyShort[];
  /** short id → owning seller id, so a "view details" action can open the right storefront. */
  sellerById: Map<string, string>;
  /** Whether the viewer has SAVED this short (commerce save == the vertical feed's "like"). */
  isLiked: (id: string) => boolean;
  /** Optimistic SAVE toggle, reconciled to server truth, rolled back on failure. */
  toggleLike: (id: string) => void;
  /** KES price label for a short (no /mo — commerce has no rent dimension). */
  priceLabelFor: (short: PropertyShort) => string;
  /** Auto-widen: true when the buyer's immediate radius had no video shorts and the feed fell back
   *  to the nearest content (mirrors the Listings surface). The page surfaces an honest distance
   *  note on the rail + overlay so the Videos lane is never a silent dead-end. */
  widened: boolean;
  /** Closest returned short's distance in metres (null ⇒ no shorts at all) — the distance-only
   *  signal for the "closest shops are within X km" note. Never a delivery claim. */
  nearestDistanceM: number | null;
  /** How many shorts the immediate (un-widened) radius held — splits the note between "nothing
   *  nearby" (0) and "only a few nearby, also showing farther" (>0) so it never claims emptiness
   *  when the buyer's own local shorts are in the list. */
  immediateCount: number;
  /** True while the initial fetch is in flight (no cached page yet). Consumers use this to swap
   *  between a shimmering skeleton and an "authoritative empty" placeholder — Chunk 1 (permanent
   *  columns) needs the shelf mounted even when shorts.length === 0. */
  isLoading: boolean;
}

/** Adapt a commerce FeedItem (that carries a video) to the PropertyShort shape the shorts UI reads.
 *  Only the fields the shorts components actually use are populated; the rest are inert defaults. */
function toShort(item: FeedItem, videoUrl: string): PropertyShort {
  return {
    id: item.id,
    title: item.title,
    price: item.price_cents / 100,
    currency: item.currency,
    // Commerce has no rent/sale dimension — the badge is suppressed for these (showListingBadge=false),
    // and the price label is overridden, so this value is never surfaced. 'sale' is an inert default.
    listing_type: 'sale',
    category: 'shop',
    // The shop name reads naturally as the "by …" line in the vertical feed.
    agent_name: item.shop_name ?? undefined,
    location_name: item.shop_name ?? '',
    main_image: undefined,
    video: { url: videoUrl },
    is_featured: false,
  };
}

export function useCommerceVideoShorts({ session, lat, lng }: UseCommerceVideoShortsArgs): CommerceVideoShorts {
  // The Videos lane of the proximity feed. A small shelf, not infinite scroll — first page is enough.
  const { items, widened, nearestDistanceM, immediateCount, isLoading } = useCommerceFeed({ session, lat, lng, kind: 'videos' });

  // One short per item, using its FIRST video URL (the feed leads with the clip). Items whose media
  // has no video are dropped. Map id→seller so actions can resolve the owning shop.
  const { shorts, sellerById } = useMemo(() => {
    const list: PropertyShort[] = [];
    const owner = new Map<string, string>();
    for (const item of items) {
      const videoUrl = item.media_urls.find((u) => isVideoUrl(u));
      if (!videoUrl) continue;
      list.push(toShort(item, videoUrl));
      owner.set(item.id, item.seller_id);
    }
    return { shorts: list, sellerById: owner };
  }, [items]);

  // §8 SAVE, tracked here because the feed item carries save_count but not the caller's own
  // saved-state. Optimistic flip, reconciled to the server's idempotent toggle, rolled back on error.
  const [savedIds, setSavedIds] = useState<Set<string>>(() => new Set());
  const inFlight = useRef<Set<string>>(new Set());

  const isLiked = useCallback((id: string) => savedIds.has(id), [savedIds]);

  const toggleLike = useCallback((id: string) => {
    if (!session) return;
    if (inFlight.current.has(id)) return;
    inFlight.current.add(id);
    const wasSaved = savedIds.has(id);
    setSavedIds((prev) => {
      const next = new Set(prev);
      if (wasSaved) next.delete(id); else next.add(id);
      return next;
    });
    toggleSave(session, id)
      .then((res) => {
        // Reconcile to the server's truth (idempotent toggle).
        setSavedIds((prev) => {
          const next = new Set(prev);
          if (res.saved) next.add(id); else next.delete(id);
          return next;
        });
      })
      .catch(() => {
        // Roll back the optimistic flip.
        setSavedIds((prev) => {
          const next = new Set(prev);
          if (wasSaved) next.add(id); else next.delete(id);
          return next;
        });
      })
      .finally(() => { inFlight.current.delete(id); });
  }, [session, savedIds]);

  const priceLabelFor = useCallback(
    (s: PropertyShort) => formatPrice(s.price, s.currency),
    [],
  );

  return { shorts, sellerById, isLiked, toggleLike, priceLabelFor, widened, nearestDistanceM, immediateCount, isLoading };
}
