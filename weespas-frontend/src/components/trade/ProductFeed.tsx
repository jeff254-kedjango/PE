// ProductFeed — the buyer's infinite proximity stream of product cards.
//
// Wires the location (useGeolocation, with an honest default fallback handled by the parent page)
// → useCommerceFeed (keyset infinite query) → ProductCard grid, plus the shared
// useConfirmedListings batch so each card's Confirmed shield is resolved in ONE call (no N+1).
// Renders explicit states for every honest outcome: loading, error, empty, and the
// location-permission prompt (the feed is location-native — we never fabricate a position).
import React, { useMemo } from 'react';
import { useCommerceFeed } from '../../hooks/useCommerceFeed';
import { useConfirmedListings } from '../../hooks/useConfirmedListings';
import ProductCard from './ProductCard';
import { widenNoteText } from './widenNote';
import type { CommerceSession, FeedItem } from '../../api/commerce';
import './ProductFeed.css';

interface ProductFeedProps {
  session: CommerceSession | null;
  lat: number | null;
  lng: number | null;
  radiusM?: number;
  /** Location permission was denied / unavailable — show the prompt instead of an empty feed. */
  locationDenied?: boolean;
  onRequestLocation?: () => void;
  onSelectSeller: (sellerId: string) => void;
}

// This is the §8 "Listings" surface — the social IMAGE timeline. It always fetches the listings lane
// (declared short-video posts live in the Videos overlay, not here), and each card renders images
// only (ProductCard filters out video slides). The Videos experience is TradePage's full-screen
// vertical overlay, not a mode of this feed.
const ProductFeed: React.FC<ProductFeedProps> = ({
  session, lat, lng, radiusM, locationDenied, onRequestLocation, onSelectSeller,
}) => {
  const {
    items, isLoading, isError, error, fetchNextPage, hasNextPage, isFetchingNextPage,
    widened, nearestDistanceM, immediateCount,
  } = useCommerceFeed({ session, lat, lng, radiusM, kind: 'listings' });

  // Honest "closest shops are within X km" note when the feed widened to surface more content (null
  // ⇒ nothing to say). immediateCount splits the copy: 0 ⇒ "nothing nearby", >0 ⇒ "only a few
  // nearby, also showing farther". Distance-only — never delivery.
  const widenNote = widenNoteText(widened, nearestDistanceM, immediateCount);

  // Batch-resolve the Confirmed shield for the listings currently loaded (one call, re-keyed when
  // the visible id set changes — see useConfirmedListings).
  const ids = useMemo(() => items.map((i) => i.id), [items]);
  const confirmed = useConfirmedListings(ids);

  if (locationDenied) {
    return (
      <div className="product-feed__state" role="status">
        <p>We need your location to show what’s selling near you.</p>
        {onRequestLocation && (
          <button type="button" className="product-feed__cta" onClick={onRequestLocation}>
            Enable location
          </button>
        )}
      </div>
    );
  }

  if (isLoading) {
    return <div className="product-feed__state" role="status">Finding what’s near you…</div>;
  }

  if (isError) {
    return (
      <div className="product-feed__state product-feed__state--error" role="alert">
        <p>Couldn’t load the feed. {error?.message ?? ''}</p>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="product-feed__state" role="status">
        Nothing on sale near you yet — check back soon.
      </div>
    );
  }

  return (
    <div className="product-feed">
      {widenNote && (
        <p className="product-feed__widen-note" role="status">{widenNote}</p>
      )}
      <div className="product-feed__column">
        {items.map((item: FeedItem) => (
          <ProductCard
            key={`${item.id}:${item.is_sponsored ? 'sp' : 'org'}`}
            item={item}
            confirmed={confirmed.has(item.id)}
            session={session}
            onSelect={(it) => onSelectSeller(it.seller_id)}
          />
        ))}
      </div>

      {hasNextPage && (
        <div className="product-feed__more">
          <button
            type="button"
            className="product-feed__cta"
            onClick={() => fetchNextPage()}
            disabled={isFetchingNextPage}
          >
            {isFetchingNextPage ? 'Loading…' : 'Show more'}
          </button>
        </div>
      )}
    </div>
  );
};

export default ProductFeed;
