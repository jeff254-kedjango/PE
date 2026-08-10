import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Property } from '../../types/propertyApi';
import { fetchFeaturedProperties } from '../../api/properties';
import { resolveMediaUrl } from '../../utils/media';
import Icon from '../ui/Icon';
import './PropertyGallery.css';

interface PropertyGalleryProps {
  selectedPropertyId: string | null;
  onSelect: (property: Property) => void;
  /** When set, featured listings are filtered/ranked relative to this location. */
  userLocation?: { latitude: number; longitude: number };
}

/** Format price as KES shorthand (e.g. "5M", "25K") */
function formatShortPrice(price?: number, currency?: string): string {
  if (!price) return '';
  const prefix = currency === 'KES' || !currency ? 'KES ' : `${currency} `;
  if (price >= 1_000_000) return `${prefix}${(price / 1_000_000).toFixed(price % 1_000_000 === 0 ? 0 : 1)}M`;
  if (price >= 1_000) return `${prefix}${(price / 1_000).toFixed(price % 1_000 === 0 ? 0 : 0)}K`;
  return `${prefix}${price}`;
}

/** Get carousel/hero image — prefer full-res URL for sharper display, thumbnail as fallback */
function getCardImage(property: Property): { src: string; srcSet?: string } | null {
  const img = property.main_image ?? property.images?.[0];
  if (!img) return null;
  const full = resolveMediaUrl(img.url);
  const thumb = resolveMediaUrl(img.thumbnail_url);
  if (full && thumb && full !== thumb) {
    return { src: full, srcSet: `${thumb} 200w, ${full} 600w` };
  }
  const src = full || thumb;
  if (!src) return null;
  return { src };
}

const AUTOPLAY_INTERVAL = 4000;
// Card geometry — must match .carousel-item size + .carousel-track gap in the CSS.
// The tiles are SQUARE, so this one scalar STEP is the per-slide travel on EITHER
// axis: the strip scrolls vertically on desktop (right rail) and horizontally on
// mobile (below the hero). JS only ever computes a scalar offset; the CSS decides
// which axis to translate it on (via `--track-offset`), so this component never
// needs to know the viewport orientation.
const CARD_SIZE = 84;
const CARD_GAP = 10;
const STEP = CARD_SIZE + CARD_GAP;
// Cap the navigation dots so a large featured set doesn't flood the hero with
// 100+ dots. The carousel still cycles through ALL listings; only the dot strip
// is windowed (a sliding range centred on the active card).
const MAX_DOTS = 10;

/**
 * Compute the sliding window of dot indices to render. Always returns ≤ MAX_DOTS
 * real indices, centred on `active` and clamped to [0, count). O(MAX_DOTS), and
 * `count <= MAX_DOTS` returns the full list unchanged. The boolean flags say
 * whether listings exist beyond each visible end (so those edge dots can be
 * shrunk to hint "more"). Pure + deterministic → trivially testable.
 */
function computeDotWindow(count: number, active: number): {
  indices: number[];
  moreBefore: boolean;
  moreAfter: boolean;
} {
  if (count <= MAX_DOTS) {
    return { indices: Array.from({ length: count }, (_, i) => i), moreBefore: false, moreAfter: false };
  }
  const half = Math.floor(MAX_DOTS / 2);
  let start = active - half;
  if (start < 0) start = 0;
  if (start > count - MAX_DOTS) start = count - MAX_DOTS;
  const indices = Array.from({ length: MAX_DOTS }, (_, i) => start + i);
  return { indices, moreBefore: start > 0, moreAfter: start + MAX_DOTS < count };
}

const PropertyGallery: React.FC<PropertyGalleryProps> = ({ selectedPropertyId, onSelect, userLocation }) => {
  const [featuredProperties, setFeaturedProperties] = useState<Property[]>([]);
  const [loading, setLoading] = useState(true);
  // `pos` is the TRACK position in card-steps, ranging 0..N (N = clone boundary).
  // The logical/highlighted card is `pos % N`. Position N renders the appended
  // clone of card 0 — pixel-identical to position 0 — so snapping N→0 with the
  // transition off is invisible and the loop never "rewinds to the front".
  const [pos, setPos] = useState(0);
  // When true the track transition is suppressed for one commit (the seamless snap).
  const [instant, setInstant] = useState(false);
  const [paused, setPaused] = useState(false);
  const touchStartX = useRef(0);
  const touchDeltaX = useRef(0);
  const autoplayTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // Fetch featured properties from API. When userLocation is set, the backend
  // restricts/ranks by proximity but NEVER returns empty (it tops up nationwide).
  const lat = userLocation?.latitude;
  const lng = userLocation?.longitude;
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const geo = lat !== undefined && lng !== undefined ? { latitude: lat, longitude: lng } : undefined;
    fetchFeaturedProperties(undefined, geo)
      .then((data) => {
        if (!cancelled) {
          setFeaturedProperties(data);
          setPos(0);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [lat, lng]);

  // Show ALL featured listings (no client-side cap — the set is admin-curated).
  const items = featuredProperties;
  const count = items.length;
  const activeIndex = count > 0 ? pos % count : 0;
  const looping = count > 1;

  // Keep `pos` valid if the list shrinks between fetches.
  useEffect(() => {
    if (count === 0) { if (pos !== 0) setPos(0); return; }
    if (pos > count) setPos(pos % count);
  }, [count]); // eslint-disable-line react-hooks/exhaustive-deps

  // Forward step — used by autoplay and "next". Lands on N at the boundary, which
  // the transition-end handler then snaps back to 0 seamlessly. We CAP growth at
  // N: if the previous boundary snap hasn't committed yet (a dropped/late
  // transitionend, or autoplay firing mid-animation), we hold at the clone of
  // card 0 (pixel-identical to 0) instead of running past 2N into blank track —
  // that runaway was the "listings disappear after the first cycle" bug.
  const goNext = useCallback(() => {
    if (!looping) return;
    setInstant(false);
    setPos((p) => (p >= count ? count : p + 1));
  }, [looping, count]);

  // Backward step — seamless: from 0, jump instantly to the clone boundary N (same
  // pixels as 0), then animate to N-1 on the next frame so it reads as a left card
  // sliding in rather than a long rewind.
  const goPrev = useCallback(() => {
    if (!looping) return;
    setPos((p) => {
      if (p > 0) { setInstant(false); return p - 1; }
      // p === 0 → snap to boundary without animation, then animate one step back.
      setInstant(true);
      requestAnimationFrame(() => {
        setInstant(false);
        requestAnimationFrame(() => setPos(count - 1));
      });
      return count;
    });
  }, [looping, count]);

  // The seamless snap: once the slide INTO the clone (pos >= count) finishes,
  // disable the transition and reset to the equivalent real position for one
  // commit, then re-enable it.
  const snapFromBoundary = useCallback(() => {
    setInstant(true);
    setPos((p) => (count > 0 ? p % count : 0));
    requestAnimationFrame(() => setInstant(false));
  }, [count]);

  // `transitionend` BUBBLES — child card hover/image transitions fire it on the
  // track too. Only react to the track's OWN transform transition; otherwise an
  // unrelated child event could trigger a snap mid-slide. Guard `>= count` (not
  // `=== count`) so even an overshoot is recovered.
  const onTrackTransitionEnd = useCallback((e: React.TransitionEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget || e.propertyName !== 'transform') return;
    if (pos >= count && count > 0) snapFromBoundary();
  }, [pos, count, snapFromBoundary]);

  // Fallback: if the boundary `transitionend` is ever dropped (tab backgrounded,
  // the browser coalescing frames, a missed event), `pos` would stay pinned at
  // the clone and the carousel would freeze. Guarantee the snap after the slide
  // duration (500ms transition + margin) so motion always resumes.
  useEffect(() => {
    if (pos < count || count === 0 || instant) return;
    const t = setTimeout(snapFromBoundary, 650);
    return () => clearTimeout(t);
  }, [pos, count, instant, snapFromBoundary]);

  // Jump straight to a specific real card (dot nav / external selection).
  const goTo = useCallback((index: number) => {
    setInstant(false);
    setPos(((index % count) + count) % count);
  }, [count]);

  // Sync highlighted card with an externally-selected property.
  useEffect(() => {
    if (loading || !count || !selectedPropertyId) return;
    const idx = items.findIndex((p) => p.id === selectedPropertyId);
    if (idx >= 0 && idx !== activeIndex) goTo(idx);
  }, [items, selectedPropertyId, loading]); // eslint-disable-line react-hooks/exhaustive-deps

  const handlePrev = useCallback(() => { setPaused(true); goPrev(); }, [goPrev]);
  const handleNext = useCallback(() => { setPaused(true); goNext(); }, [goNext]);

  // --- Auto-scroll: advances every 4s unless paused. Latest goNext via ref so the
  // interval isn't torn down/recreated on every position change. ---
  const goNextRef = useRef(goNext);
  goNextRef.current = goNext;
  useEffect(() => {
    if (paused || loading || !looping) {
      if (autoplayTimer.current) { clearInterval(autoplayTimer.current); autoplayTimer.current = null; }
      return;
    }
    autoplayTimer.current = setInterval(() => goNextRef.current(), AUTOPLAY_INTERVAL);
    return () => {
      if (autoplayTimer.current) { clearInterval(autoplayTimer.current); autoplayTimer.current = null; }
    };
  }, [paused, loading, looping]);

  // Pause on hover
  const handleMouseEnter = () => setPaused(true);
  const handleMouseLeave = () => setPaused(false);

  // Pause on card click (user interaction)
  const handleCardClick = (index: number, property: Property) => {
    setPaused(true);
    goTo(index);
    onSelect(property);
  };

  // NOTE: no global keyboard (arrow-key) navigation. A window-level keydown listener made the
  // gallery hijack arrow keys while the user was merely scrolling/paging the document, which is
  // surprising. Navigation is driven only by the explicit prev/next chevrons, tile clicks, touch
  // swipe and autoplay — all scoped to direct user intent on the component itself.

  // Touch swipe handlers
  const onTouchStart = (e: React.TouchEvent) => {
    touchStartX.current = e.touches[0].clientX;
    touchDeltaX.current = 0;
    setPaused(true);
  };
  const onTouchMove = (e: React.TouchEvent) => {
    touchDeltaX.current = e.touches[0].clientX - touchStartX.current;
  };
  const onTouchEnd = () => {
    const threshold = 50;
    if (touchDeltaX.current > threshold) handlePrev();
    else if (touchDeltaX.current < -threshold) handleNext();
    touchDeltaX.current = 0;
  };

  const featured = items[activeIndex];
  const heroImage = featured ? getCardImage(featured) : null;
  // Scalar per-frame travel. The track is square-tiled, so this one value is the
  // offset on whichever axis the CSS translates (`translateY` on the desktop right
  // rail, `translateX` on the mobile strip). Exposed as a CSS custom property so the
  // JS stays axis-agnostic — see .carousel-track in the CSS.
  const trackOffset = -(pos * STEP);

  // For a seamless forward loop we render the list ONCE more after itself; the
  // appended copy fills the viewport while the track slides past the last real
  // card and before the snap. (DOM is O(n); per-frame work stays O(1).)
  const trackItems = looping ? items.concat(items) : items;

  // Windowed dots: render at most MAX_DOTS, sliding to keep the active card in
  // view, so 100+ listings don't overcrowd the hero. Memoized on (count, active).
  const dotWindow = useMemo(() => computeDotWindow(count, activeIndex), [count, activeIndex]);

  if (loading) {
    return (
      <div className="gallery-card gallery-skeleton" aria-busy="true">
        <div className="hero-skeleton">
          <div className="hero-skeleton__content">
            <div className="skel-line skel-eyebrow" />
            <div className="skel-line skel-title" />
            <div className="skel-line skel-subtitle" />
            <div className="skel-line skel-body" />
          </div>
        </div>
        <div className="carousel-skeleton">
          {Array.from({ length: 5 }).map((_, index) => (
            <div key={index} className="thumbnail-skeleton">
              <div className="skel-thumb-img" />
              <div className="skel-thumb-text">
                <div className="skel-line" />
                <div className="skel-line skel-short" />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (!count) return null;

  return (
    <section
      className="gallery-wrapper"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      onTouchStart={onTouchStart}
      onTouchMove={onTouchMove}
      onTouchEnd={onTouchEnd}
    >
      {/* gallery-card has overflow:hidden for border-radius clipping. The hero now
          fills the whole card at a fixed 16:9 stage; the clickable strip floats
          over the hero (right rail on desktop, bottom strip on mobile). */}
      <div className="gallery-card">
        <div className="gallery-stage">
          {/* ── Hero (main display) — fills the stage ── */}
          <div className="gallery-hero" aria-label="Featured property">
            {heroImage ? (
              <img
                key={featured?.id}
                className="gallery-hero__image"
                src={heroImage.src}
                srcSet={heroImage.srcSet}
                sizes="100vw"
                alt={featured?.main_image?.alt_text ?? featured?.title ?? 'Featured property'}
                loading="eager"
                decoding="async"
              />
            ) : (
              <div className="gallery-hero__placeholder" />
            )}
            <div className="hero-gradient" />

            {/* Dot navigation overlaid on hero — windowed to MAX_DOTS so a large
                featured set doesn't flood the container. Edge dots are shrunk
                (--edge) when more listings exist beyond the visible range. */}
            <div className="carousel-dots" role="tablist" aria-label="Property slides">
              {dotWindow.indices.map((i, slot) => {
                const isEdge =
                  (dotWindow.moreBefore && slot === 0) ||
                  (dotWindow.moreAfter && slot === dotWindow.indices.length - 1);
                return (
                  <button
                    key={i}
                    role="tab"
                    aria-selected={i === activeIndex}
                    aria-label={`Slide ${i + 1} of ${count}`}
                    className={`carousel-dot ${i === activeIndex ? 'active' : ''} ${isEdge ? 'carousel-dot--edge' : ''}`}
                    onClick={() => { setPaused(true); goTo(i); }}
                  />
                );
              })}
            </div>

            <div className="gallery-hero__content">
              <span className="eyebrow">{userLocation ? 'Featured stay near you' : 'Featured listings'}</span>
              <h2>{featured?.title || 'Handpicked property'}</h2>

              <div className="hero-meta">
                {featured?.listing_type && (
                  <span className={`hero-badge hero-badge--${featured.listing_type}`}>
                    For {featured.listing_type}
                  </span>
                )}
                {featured?.distance !== undefined && featured?.distance !== null && (
                  <span className="hero-distance">{featured.distance.toFixed(1)} km away</span>
                )}
                {featured?.price ? (
                  <span className="hero-price">{formatShortPrice(featured.price, featured.currency)}</span>
                ) : null}
              </div>

              <p className="hero-copy">
                {featured?.description || 'Discover one of the best local experiences in your area.'}
              </p>
            </div>
          </div>

          {/* ── Clickable strip — floats on the hero's right (desktop) / below (mobile).
              Nav buttons scroll the strip only; the hero image crossfades on select.
              Only shown when looping (a lone listing needs no strip). ── */}
          {looping && (
            <div className="carousel-rail">
              <button
                type="button"
                className="carousel-nav carousel-nav--prev"
                onClick={handlePrev}
                aria-label="Previous listings"
              >
                <Icon name="chevronUp" size={18} />
              </button>

              <div className="carousel-viewport">
                <div
                  className={`carousel-track ${instant ? 'carousel-track--instant' : ''}`}
                  style={{ ['--track-offset' as string]: `${trackOffset}px` }}
                  onTransitionEnd={onTrackTransitionEnd}
                  role="tabpanel"
                >
                  {trackItems.map((property, index) => {
                    const realIndex = index % count;
                    const isClone = index >= count;
                    const cardImg = getCardImage(property);
                    return (
                      <button
                        key={`${property.id}-${index}`}
                        type="button"
                        className={`carousel-item ${!isClone && realIndex === activeIndex ? 'active' : ''}`}
                        data-index={realIndex}
                        aria-hidden={isClone}
                        tabIndex={isClone ? -1 : 0}
                        onClick={() => handleCardClick(realIndex, property)}
                        aria-label={`View ${property.title}`}
                      >
                        {cardImg ? (
                          <img
                            className="thumbnail-visual__img"
                            src={cardImg.src}
                            srcSet={cardImg.srcSet}
                            sizes="84px"
                            alt={property.main_image?.alt_text ?? property.title}
                            loading="lazy"
                            decoding="async"
                          />
                        ) : (
                          <span className="thumbnail-visual__letter">{property.title.slice(0, 1)}</span>
                        )}
                        {/* Price + category share one bottom row (space-between); the
                            title is omitted (it already shows on the hero, req 4). */}
                        <div className="thumbnail-tags">
                          {property.price ? (
                            <span className="thumbnail-price">
                              {formatShortPrice(property.price, property.currency)}
                            </span>
                          ) : <span />}
                          {property.category && (
                            <span className="thumbnail-category">{property.category}</span>
                          )}
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>

              <button
                type="button"
                className="carousel-nav carousel-nav--next"
                onClick={handleNext}
                aria-label="More listings"
              >
                <Icon name="chevronDown" size={18} />
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
};

export default PropertyGallery;
