// FlashSales — the §8 nationwide "crazy offer" grid, directly under Quick Buys in the Trade right
// rail. A 3-column × 2-row paged grid (6/page) with prev/next nav (bottom-right, matching Quick
// Buys / the ShopVideoStrip shelf). The title carries a fixed subtitle, "expires in less than an
// hour" — every flash sale is a ≤1-hour window.
//
// Data: useFlashSales → the whole nationwide slate ranked by craziness (a precomputed margin, see
// commerce services/flash_sales.py). Not personal, not location-scoped (a Kisumu sale shows in
// Nairobi); lat/lng only add a display-only distance. The component PAGES over the bounded slate
// locally (no per-page refetch). There is NO filter — the slate is the platform's craziest offers.
//
// Chunk 1 (permanent columns): the section stays MOUNTED even when there's nothing on flash right
// now, so the right column keeps its width on load. Shimmering skeleton grid while the initial
// fetch is in flight; an honest "No flash sales right now" placeholder once the server confirms
// empty. Header + title stay visible in the empty state so the column has a stable anchor; the
// "expires in less than an hour" subtitle is REPLACED with an honest empty-state hint (the
// urgency claim is misleading when the slate is empty).
import React, { useMemo, useState } from 'react';
import Icon from '../ui/Icon';
import FlashSaleCard from './FlashSaleCard';
import { useFlashSales } from '../../hooks/useFlashSales';
import type { CommerceSession } from '../../api/commerce';
import './FlashSales.css';

interface FlashSalesProps {
  session: CommerceSession | null;
  lat: number;
  lng: number;
  onSelectSeller: (sellerId: string) => void;
}

const FlashSales: React.FC<FlashSalesProps> = ({ session, lat, lng, onSelectSeller }) => {
  const [page, setPage] = useState(0);

  const { data, isLoading } = useFlashSales({ session, lat, lng });
  const items = data?.items ?? [];
  const pageSize = data?.page_size ?? 6;
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));

  // Clamp the page if the slate shrank (an expiry can drop items between polls) — never a blank page
  // past the end. Derived, so it self-corrects on every render without an effect.
  const safePage = Math.min(page, pageCount - 1);
  const pageItems = useMemo(
    () => items.slice(safePage * pageSize, safePage * pageSize + pageSize),
    [items, safePage, pageSize],
  );

  // Chunk 1: stay MOUNTED unconditionally. Three visual states:
  //   - items present  → real grid + urgency subtitle
  //   - items empty + fetch in flight → skeleton grid (subtitle → "Loading…")
  //   - items empty + settled          → placeholder text (subtitle → "None right now")
  const showSkeleton = isLoading && items.length === 0;
  const authoritativeEmpty = !isLoading && items.length === 0;

  return (
    <section className="flash-sales" aria-label="Flash Sales">
      <header className="flash-sales__header">
        <h3 className="flash-sales__title">
          <Icon name="bolt" size={15} /> Flash Sales
        </h3>
        {/* The urgency line ("expires in less than an hour") is only honest when there IS a sale on
            display. Swap it in the empty states so we never make a time-claim about nothing. */}
        <p className="flash-sales__subtitle">
          {items.length > 0
            ? 'expires in less than an hour'
            : showSkeleton
            ? 'Loading…'
            : 'None right now'}
        </p>
      </header>

      {showSkeleton ? (
        <div
          className="flash-sales__grid flash-sales__grid--skeleton"
          data-testid="flash-sales-skeleton"
          aria-hidden="true"
        >
          {/* Six 1:1 tiles matching the real 3×2 grid page so the section height is stable. */}
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="skeleton flash-sales__tile-skeleton" />
          ))}
        </div>
      ) : authoritativeEmpty ? (
        <p className="flash-sales__empty" data-testid="flash-sales-empty">
          No flash sales in the pipeline — new ones appear here as they light up.
        </p>
      ) : (
        <div className="flash-sales__grid" data-testid="flash-sales-grid">
          {pageItems.map((item) => (
            <FlashSaleCard key={item.id} item={item} session={session} onSelectSeller={onSelectSeller} />
          ))}
        </div>
      )}

      {/* Prev/next nav — bottom-right, matching Quick Buys. Steps whole pages; disables at bounds;
          hidden when a single page fits everything. */}
      {pageCount > 1 && (
        <div className="flash-sales__nav">
          <span className="flash-sales__page" aria-live="polite">{safePage + 1} / {pageCount}</span>
          <button
            type="button"
            className="flash-sales__nav-btn"
            onClick={() => setPage(Math.max(0, safePage - 1))}
            disabled={safePage === 0}
            aria-label="Previous Flash Sales"
          >
            <Icon name="chevronLeft" size={18} />
          </button>
          <button
            type="button"
            className="flash-sales__nav-btn"
            onClick={() => setPage(Math.min(pageCount - 1, safePage + 1))}
            disabled={safePage >= pageCount - 1}
            aria-label="Next Flash Sales"
          >
            <Icon name="chevronRight" size={18} />
          </button>
        </div>
      )}
    </section>
  );
};

export default FlashSales;
