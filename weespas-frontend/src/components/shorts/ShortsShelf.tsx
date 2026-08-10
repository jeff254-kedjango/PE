// src/components/shorts/ShortsShelf.tsx
import React, { useCallback, useEffect, useRef, useState } from 'react';
import ShortCard from './ShortCard';
import Icon from '../ui/Icon';
import type { PropertyShort } from '../../api/shorts';
import './ShortsShelf.css';

interface ShortsShelfProps {
  /** Pre-filtered shorts to display (caller handles dismissals). */
  shorts: PropertyShort[];
  isLoading: boolean;
  isError: boolean;
  onSelect: (id: string) => void;
  onDismiss?: (id: string) => void;
  onSeeAll: () => void;
  /** Heading rendered for this shelf (allows differentiation between pages). */
  heading?: string;
  /** Hide the whole header row (eyebrow + heading + See-all) — used where the shelf is a bare strip
   *  inside another column (commerce Trade). Default: header shown. */
  hideHeader?: boolean;
  /** Extra root class (e.g. a tighter visible-count variant for a narrow column). */
  className?: string;
  /** Forwarded to each ShortCard — show the FOR SALE/FOR RENT badge. Default on (real-estate). */
  showListingBadge?: boolean;
  /** Per-card price label override (commerce passes its own KES formatting). */
  priceLabelFor?: (short: PropertyShort) => string;
  /** Use the video's first frame as the tile still when there's no image poster (commerce). */
  posterFromVideo?: boolean;
}

/**
 * Horizontal Shorts shelf — presentational only.
 *
 * Performance:
 * - No data fetching here; parent owns the shorts query and passes a slice.
 *   Mounting many shelves is cheap (no duplicate query subscriptions).
 * - Smooth scroll uses the platform-native `scrollBy({ behavior: 'smooth' })`.
 *   We do NOT animate with rAF/JS — the compositor handles it.
 * - Arrow visibility derives from a passive scroll listener that toggles two
 *   boolean states (no re-render storm: only when crossing the boundary).
 * - `requestAnimationFrame`-throttled boundary check.
 */
const ShortsShelf: React.FC<ShortsShelfProps> = ({
  shorts,
  isLoading,
  isError,
  onSelect,
  onDismiss,
  onSeeAll,
  heading = 'Watch homes near you',
  hideHeader = false,
  className,
  showListingBadge = true,
  priceLabelFor,
  posterFromVideo = false,
}) => {
  const visible = shorts;
  const trackRef = useRef<HTMLDivElement | null>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);
  const tickingRef = useRef(false);

  const updateArrows = useCallback(() => {
    const el = trackRef.current;
    if (!el) return;
    const left = el.scrollLeft > 4;
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 4;
    setCanScrollLeft((prev) => (prev === left ? prev : left));
    setCanScrollRight((prev) => (prev === right ? prev : right));
  }, []);

  useEffect(() => {
    const el = trackRef.current;
    if (!el) return;
    updateArrows();
    const onScroll = () => {
      if (tickingRef.current) return;
      tickingRef.current = true;
      requestAnimationFrame(() => {
        tickingRef.current = false;
        updateArrows();
      });
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    const ro = new ResizeObserver(updateArrows);
    ro.observe(el);
    return () => {
      el.removeEventListener('scroll', onScroll);
      ro.disconnect();
    };
  }, [updateArrows, visible.length]);

  const scrollByCard = useCallback((dir: -1 | 1) => {
    const el = trackRef.current;
    if (!el) return;
    // Scroll by ~one viewport's worth minus one card so the previously-last
    // card becomes the new first — feels continuous, never jumps a card.
    const step = Math.max(el.clientWidth * 0.85, 200);
    el.scrollBy({ left: dir * step, behavior: 'smooth' });
  }, []);

  if (isError) return null;
  if (!isLoading && visible.length === 0) return null;

  return (
    <section className={`shorts-shelf${className ? ` ${className}` : ''}`} aria-label="Short video listings">
      {!hideHeader && (
        <div className="shorts-shelf__header">
          <div>
            <p className="eyebrow">Shorts</p>
            <h3>{heading}</h3>
          </div>
          <button type="button" className="shorts-shelf__see-all" onClick={onSeeAll}>
            See all <Icon name="chevronRight" size={14} />
          </button>
        </div>
      )}

      <div className="shorts-shelf__viewport">
        <div className="shorts-shelf__track" role="list" ref={trackRef}>
          {isLoading
            ? Array.from({ length: 4 }).map((_, i) => (
                <div key={`skel-${i}`} className="shorts-shelf__skeleton" aria-hidden="true" />
              ))
            : visible.map((short) => (
                <div role="listitem" key={short.id}>
                  <ShortCard
                    short={short}
                    onSelect={onSelect}
                    onDismiss={onDismiss}
                    showListingBadge={showListingBadge}
                    priceLabel={priceLabelFor?.(short)}
                    posterFromVideo={posterFromVideo}
                  />
                </div>
              ))}
        </div>
      </div>

      {/* Nav lives BELOW the viewport so the chevrons never overlap card art.
          Hidden on touch via CSS — touch users swipe the track directly. */}
      <div
        className="shorts-shelf__nav"
        aria-hidden={!canScrollLeft && !canScrollRight}
      >
        <button
          type="button"
          className="shorts-shelf__nav-btn"
          onClick={() => scrollByCard(-1)}
          disabled={!canScrollLeft}
          aria-label="Scroll shorts left"
        >
          <Icon name="chevronLeft" size={18} />
        </button>
        <button
          type="button"
          className="shorts-shelf__nav-btn"
          onClick={() => scrollByCard(1)}
          disabled={!canScrollRight}
          aria-label="Scroll shorts right"
        >
          <Icon name="chevronRight" size={18} />
        </button>
      </div>
    </section>
  );
};

// Custom equality: compare shorts by id-sequence so re-renders from unrelated
// parent state (modal open, search query) don't re-render the shelf when the
// data is identical. Function props are expected to be stable from the caller.
export default React.memo(ShortsShelf, (prev, next) => {
  if (prev.isLoading !== next.isLoading) return false;
  if (prev.isError !== next.isError) return false;
  if (prev.heading !== next.heading) return false;
  if (prev.hideHeader !== next.hideHeader) return false;
  if (prev.className !== next.className) return false;
  if (prev.showListingBadge !== next.showListingBadge) return false;
  if (prev.posterFromVideo !== next.posterFromVideo) return false;
  if (prev.shorts.length !== next.shorts.length) return false;
  for (let i = 0; i < prev.shorts.length; i++) {
    if (prev.shorts[i].id !== next.shorts[i].id) return false;
  }
  // isDismissed/onSelect/onDismiss/onSeeAll: only matter if user dismisses a
  // visible item — that mutates dismissals → parent recomputes `shorts` slice
  // (different ids) → caught above. Treat as stable.
  return true;
});
