// TrendingRail — the §8 "trending" board: a fixed, right-of-feed card of BOOSTED PRODUCTS.
//
// What it shows: the deterministic queue from useTrending (boosted LISTINGS reaching the buyer's
// locality), rendered as a set of CATEGORY-COLORED product cards (title + price + an image — the
// lunchtime "Nyama Choma / KES 350 / 🥩"). The lead visual is the PRODUCT'S OWN photo when the
// listing has one (the promotion shows the item for sale), else the category glyph. Tapping a card
// opens that seller's storefront.
//
// The animation is a PER-SLOT DECAY BOARD (useTrendingRotation), not a scroll: a fixed set of slots,
// each decaying on its own staggered timer, the next queued product taking a freed slot. Any slot
// can flip independently. Decay PAUSES on hover/focus-within so a buyer can read/tap a card without
// it vanishing under them; prefers-reduced-motion freezes the rotation entirely (handled in the hook
// + the css). Boosted products appear in BOTH this rail AND the in-feed sponsored lane (by design).
//
// Hidden under 1100px (mobile/tablet keep the single full-width feed column — see TrendingRail.css).
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTrending } from '../../hooks/useTrending';
import { useTrendingRotation } from '../../hooks/useTrendingRotation';
import { useFitCount } from '../../hooks/useFitCount';
import { categoryColor, categoryLabel } from '../../utils/categories';
import { formatDistance, formatPrice, type CommerceSession, type TrendingProductCard } from '../../api/commerce';
import { resolveMediaUrl } from '../../utils/media';
import CategoryIcon from './CategoryIcon';
import Icon from '../ui/Icon';
import './TrendingRail.css';

// One product row's footprint, in px, kept in lockstep with TrendingRail.css. The board fits
// floor((boardHeight + GAP) / (ROW_H + GAP)) of these — that fitted count is how many fixed,
// non-scrolling slots this viewport gets. (Measured: card ≈ 55px tall after the 6px vertical
// padding trim, --space-2 = 8px gap. The tighter footprint fits one extra slot per board — e.g. 6
// instead of 5 on a ~600px-tall window. These two MUST match TrendingRail.css's .trending-rail__card
// padding + .trending-rail__board gap, or the fit math drifts.)
const ROW_H = 55;
const ROW_GAP = 8;
// Never show zero cards when there's room, and never measure past the server's candidate cap.
const MIN_SLOTS = 1;

interface TrendingRailProps {
  session: CommerceSession | null;
  lat: number;
  lng: number;
  /** Open a seller's storefront (reuses the page's existing storefront panel — same path a feed
   *  card tap uses). Receives the product's seller_id. */
  onSelectSeller: (sellerId: string) => void;
}

const TrendingRail: React.FC<TrendingRailProps> = ({ session, lat, lng, onSelectSeller }) => {
  const { data } = useTrending(session, lat, lng);
  const queue = data?.cards ?? [];
  // The server's candidate cap is the UPPER bound on how many cards we'd ever show; the actual
  // number of fixed slots is how many fit THIS viewport (measured below), so the board never scrolls.
  const serverCap = data?.visible_slots ?? 0;
  const slotSeconds = data?.slot_seconds ?? 12;

  // Pause the decay while the buyer is hovering or keyboard-focused inside the rail, so a card can't
  // vanish mid-read / mid-tap (the old marquee paused on hover for the same reason).
  const [paused, setPaused] = useState(false);

  // #7 — LOCALIZED search. A title filter over the ALREADY-FETCHED queue: no refetch, no new network
  // call, its effect scoped to this rail alone. `searchOpen` toggles the input in the head; `search`
  // is the live query. An empty/whitespace query is a no-op (the full queue shows).
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState('');
  const searchInputRef = useRef<HTMLInputElement>(null);

  const filteredQueue = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return queue;
    return queue.filter((c) => c.title.toLowerCase().includes(q));
  }, [queue, search]);

  // Measure the board and derive the fixed slot count for this viewport (no scroll). Capped by the
  // server's candidate cap — no point showing more slots than the queue can ever supply.
  const boardRef = useRef<HTMLUListElement>(null);
  const fitted = useFitCount(boardRef, { rowHeight: ROW_H, gap: ROW_GAP, min: MIN_SLOTS, max: serverCap || MIN_SLOTS });
  const visibleSlots = serverCap > 0 ? fitted : 0;

  // The per-slot decay engine turns the (filtered) queue into the cards visible right now. Hook is
  // always called (rules-of-hooks) — it simply returns [] for an empty queue.
  const cards = useTrendingRotation(filteredQueue, visibleSlots, slotSeconds, paused);

  // Focus the input the moment the search field opens (keyboard-first), so the buyer types straight
  // in. Runs only on the open transition.
  useEffect(() => {
    if (searchOpen) searchInputRef.current?.focus();
  }, [searchOpen]);

  const toggleSearch = useCallback(() => {
    setSearchOpen((open) => {
      // Closing the field clears the query so the full queue is restored (no lingering hidden filter).
      if (open) setSearch('');
      return !open;
    });
  }, []);

  // Chunk 1 (permanent columns): the rail STAYS MOUNTED even when the locality has no boosts, so
  // the left column keeps its 248px width on load instead of collapsing and reflowing the page. A
  // truly-empty queue renders a skeleton board (shimmering shape) while the network settles, and
  // once we have an authoritative empty answer we swap to an honest placeholder message.
  //
  // The SEARCH toggle is hidden in the empty state — the search is a client-side filter over the
  // already-fetched queue, so with zero items there is nothing to search (rule 4: no dead
  // affordance). The head keeps the title so the column still reads as "Trending near you".
  const isEmpty = queue.length === 0;
  // `data === undefined` while the initial fetch is in flight AND ALSO when the query is disabled
  // (useTrending gates on `!!session`). We only want the skeleton to shimmer when a fetch is
  // genuinely pending — not indefinitely on the no-session path. Split the empty visual on the
  // narrower boundary "session present AND data still undefined".
  const isLoading = !!session && data === undefined;
  const searching = search.trim() !== '';

  // No session ⇒ no locality to trend in. Bail after all hooks have been called so we don't
  // violate rules-of-hooks. TradePage already gates the whole layout on `session &&`, so this
  // only fires from isolated tests today — but the invariant belongs here (a rail with no session
  // has nothing honest to say).
  if (!session) return null;

  return (
    <aside
      className="trending-rail"
      aria-label="Trending boosted products near you"
      data-testid="trending-rail"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocusCapture={() => setPaused(true)}
      onBlurCapture={() => setPaused(false)}
    >
      <div className="trending-rail__head">
        <div className="trending-rail__head-row">
          <span className="trending-rail__title">Trending near you</span>
          {/* Search toggle on the FAR RIGHT of the title row (the spec's placement). Toggles the
              localized title filter below — client-side over the fetched queue, no refetch.
              Hidden when the queue is empty: there's nothing to search, and rule 4 forbids a
              button that can only ever say "no matches". */}
          {!isEmpty && (
            <button
              type="button"
              className={`trending-rail__search-btn ${searchOpen ? 'is-active' : ''}`}
              onClick={toggleSearch}
              aria-label={searchOpen ? 'Close search' : 'Search trending products'}
              aria-expanded={searchOpen}
              data-testid="trending-search-toggle"
            >
              <Icon name={searchOpen ? 'x' : 'search'} size={15} />
            </button>
          )}
        </div>
        {searchOpen && !isEmpty ? (
          <div className="trending-rail__search">
            <Icon name="search" size={14} className="trending-rail__search-icon" />
            <input
              ref={searchInputRef}
              type="search"
              className="trending-rail__search-input"
              placeholder="Search these products"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search trending products"
              data-testid="trending-search-input"
            />
          </div>
        ) : (
          <span className="trending-rail__sub">
            {isEmpty
              ? (isLoading ? 'Loading…' : 'quiet in your area')
              : `${data?.active_count ?? cards.length} boosting now`}
          </span>
        )}
      </div>
      {/* The board: a column of product slots. Each card decays/refreshes in place; the timer pauses
          on hover/focus-within (handled in the rotation hook via the rail's interaction, and in CSS
          for the visual transition). aria-live="off": swaps are silent (a live region would spam).
          When empty, the board is replaced by either a shimmering skeleton (initial fetch in
          flight) or a static placeholder (authoritative empty). Same <ul> element+testid so layout
          tests see a board regardless of state. */}
      <ul
        className={`trending-rail__board${isEmpty ? ' trending-rail__board--empty' : ''}`}
        data-testid="trending-rail-board"
        aria-live="off"
        ref={boardRef}
      >
        {isEmpty ? (
          isLoading ? (
            // Shimmering skeleton rows — same footprint as a real card (55px + 8px gap), so the
            // rail's fit-count math measures the same board when data lands. aria-hidden: purely
            // visual, screen readers already hear "Loading…" from the head sub-line.
            <SkeletonBoard aria-hidden="true" />
          ) : (
            <li className="trending-rail__placeholder" data-testid="trending-rail-empty">
              <Icon name="bolt" size={20} className="trending-rail__placeholder-icon" />
              <span className="trending-rail__placeholder-title">Nothing trending nearby</span>
              <span className="trending-rail__placeholder-sub">
                Boosted products in your area will show up here.
              </span>
            </li>
          )
        ) : (
          cards.map((card) => (
            <TrendingCard
              key={card.listing_id}
              card={card}
              onSelect={() => onSelectSeller(card.seller_id)}
            />
          ))
        )}
      </ul>
      {/* A search that matches nothing keeps the rail mounted with a hint (so the buyer sees the
          result and can clear it), instead of collapsing the whole gutter. Only reachable while the
          rail has items (the search toggle is hidden when isEmpty), but the guard keeps the
          invariant explicit. */}
      {!isEmpty && searching && filteredQueue.length === 0 && (
        <p className="trending-rail__empty" data-testid="trending-search-empty">No matches here.</p>
      )}
    </aside>
  );
};

interface TrendingCardProps {
  card: TrendingProductCard;
  onSelect: () => void;
}

const TrendingCard: React.FC<TrendingCardProps> = ({ card, onSelect }) => {
  const color = categoryColor(card.category);
  const label = categoryLabel(card.category);
  const price = formatPrice(card.price_cents, card.currency);
  // The product's own photo leads the card when the listing has one — the promotion shows the item
  // for sale, not just a category tint. Falls back to the category icon when absent.
  const image = resolveMediaUrl(card.image_url);
  // The card has no shop name now — build an explicit accessible name from title + price + category
  // (color/icon alone are not announceable).
  const ariaLabel = `${card.title}, ${price}${label ? `, ${label}` : ''}. Boosted. Tap to view shop.`;
  return (
    <li className="trending-rail__item">
      <button
        type="button"
        className="trending-rail__card"
        onClick={onSelect}
        // The category color drives a left accent bar + a tinted background + the icon color, set via
        // a CSS var so the stylesheet owns the (contrast-tuned) opacity/derivation treatment.
        style={{ ['--cat-color' as string]: color }}
        aria-label={ariaLabel}
        data-testid="trending-card"
      >
        <span className="trending-rail__icon" aria-hidden="true">
          {image
            ? <img className="trending-rail__logo" src={image} alt="" loading="lazy" />
            : <CategoryIcon category={card.category} size={20} />}
        </span>
        <span className="trending-rail__meta">
          <span className="trending-rail__name">{card.title}</span>
          <span className="trending-rail__row">
            <span className="trending-rail__price">{price}</span>
            <span className="trending-rail__dist">{formatDistance(card.distance_m)}</span>
          </span>
        </span>
        {/* Boost disclosure: a compact accent "spark" pinned to the corner, OUT of the flex flow so
            the title/price reclaim the full card width (the old pill reserved ~50px and truncated
            the title). The honesty contract is kept — "Boosted" is in the card's aria-label (read
            once by SRs) and the `title` gives sighted users a hover tooltip; the glyph is
            aria-hidden + pointer-events:none (decorative; the whole card is the click target). */}
        <span className="trending-rail__spark" title="Boosted" aria-hidden="true">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" focusable="false">
            <path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z" />
          </svg>
        </span>
      </button>
    </li>
  );
};

// SkeletonBoard — a small fleet of shimmer rows that mirrors the real TrendingCard footprint.
// Row count is FIXED at 4 (not measured from the board): a shimmer is a load hint, not a real
// slot count, so the fit-count hook is irrelevant here. Uses the shared `.skeleton` utility from
// styles/animations.css — the global reduced-motion guard in styles/reset.css freezes the
// animation automatically, so no per-component media query is needed.
const SKELETON_ROWS = 4;
const SkeletonBoard: React.FC<{ 'aria-hidden'?: boolean | 'true' | 'false' }> = (props) => (
  <>
    {Array.from({ length: SKELETON_ROWS }).map((_, i) => (
      <li key={i} className="trending-rail__item trending-rail__item--skeleton" {...props}>
        <div className="trending-rail__card trending-rail__card--skeleton">
          <span className="skeleton trending-rail__skeleton-icon" />
          <span className="trending-rail__meta">
            <span className="skeleton trending-rail__skeleton-name" />
            <span className="skeleton trending-rail__skeleton-row" />
          </span>
        </div>
      </li>
    ))}
  </>
);

export default TrendingRail;
