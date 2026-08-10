/* ==========================================================================
   NAVBAR SEARCH — the global unified search (Properties + Trade), INLINE.

   A YouTube-style search box that lives in the navbar: type and the matches drop straight down as a
   list UNDER the box (no modal). One text box searches BOTH weespas properties (their FTS) and
   commerce trade listings (GET /api/v1/search) — queried CONCURRENTLY and merged client-side, never
   cross-DB-joined (architecture doc §3). Results show in ONE dropdown with two sections, "Homes" and
   "Shops & Products", so the box doubles as a quick intro to what the platform offers.

   Two render variants share all the logic:
     • inline  — desktop: a compact box in the navbar row; the dropdown is absolutely anchored below
                  it and dismisses on outside-click / Escape / select.
     • overlay — mobile: the navbar row is too tight for a box, so the magnifier expands a full-width
                  bar under the navbar (with a light backdrop); same dropdown, same logic.

   Trade results are ranked nearest-first from the buyer's location (geolocation, falling back to the
   Kilimani demo centroid — the same source TradePage uses); properties come back by FTS relevance.
   Clicking a property → /properties/:id; a trade result → the seller storefront
   /trade/sellers/:sellerId (the same targets the in-app cards use). Trade search needs a commerce
   session, so its section only appears for signed-in users; property search is public.
   ========================================================================== */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useDebounce } from '../../hooks/useDebounce';
import { useTextSearch } from '../../hooks/useTextSearch';
import { useTradeSearch } from '../../hooks/useTradeSearch';
import { useCommerceSession } from '../../hooks/useCommerceSession';
import { useGeolocation } from '../../hooks/useGeolocation';
import { resolveMediaUrl } from '../../utils/media';
import { categoryColor, categoryLabel } from '../../utils/categories';
import { formatPrice } from '../../utils/format';
import type { Property } from '../../types/propertyApi';
// Trade prices are integer MINOR units (cents); the commerce formatPrice divides by 100. Properties
// carry MAJOR units and use utils/format's formatPrice. Two units, two formatters — aliased so each
// row renders its own money correctly (passing cents to the property formatter would render 100×).
import { formatPrice as formatTradePrice, type TradeSearchResult } from '../../api/commerce';
import Icon from '../ui/Icon';
import './NavbarSearch.css';

// Trade search proximity fallback — the Kilimani demo centroid, identical to TradePage's DEFAULT_*
// so a signed-in user who hasn't granted geolocation still gets sensibly-ranked trade results near
// the demo coverage. Location only ORDERS trade results (never gates them), so this is a safe
// default: worst case the nearest-first ordering is centred on Kilimani rather than the true spot.
const FALLBACK_LAT = -1.2907;
const FALLBACK_LNG = 36.7895;

const MIN_QUERY_LEN = 2;

const PLACEHOLDER = 'Search Houses, Shops, Products…';

type Variant = 'inline' | 'overlay';

interface NavbarSearchProps {
  /** Whether a user is signed in — trade search needs a commerce session, so the Shops & Products
   *  section is only offered to authenticated users. Property search is public. */
  isAuthenticated: boolean;
  /** 'inline' (desktop box) | 'overlay' (mobile expanded bar). Defaults to inline. */
  variant?: Variant;
  /** overlay only — dismiss the mobile bar (backdrop / ✕ / Escape / select). */
  onClose?: () => void;
}

const NavbarSearch: React.FC<NavbarSearchProps> = ({ isAuthenticated, variant = 'inline', onClose }) => {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState('');
  // Inline dropdown visibility. The overlay variant is always "open" (it IS the dropdown); the
  // inline variant opens on focus and closes on outside-click / Escape / select.
  const [focused, setFocused] = useState(false);
  const debouncedQuery = useDebounce(query, 300);

  // Buyer location for the trade proximity ranking. useGeolocation is passive (we do NOT prompt —
  // a permission popup from a search box is jarring); we use a previously-granted position if the
  // app already has one, else the demo centroid.
  const { latitude, longitude } = useGeolocation();
  const lat = latitude ?? FALLBACK_LAT;
  const lng = longitude ?? FALLBACK_LNG;

  const { session } = useCommerceSession();
  const {
    properties, isLoading: propsLoading, isError: propsError,
  } = useTextSearch(debouncedQuery);
  const {
    results: tradeResults, isLoading: tradeLoading, isError: tradeError,
  } = useTradeSearch(isAuthenticated ? session : null, debouncedQuery, lat, lng);

  const hasQuery = debouncedQuery.trim().length >= MIN_QUERY_LEN;
  const canSearchTrade = isAuthenticated;
  const isOverlay = variant === 'overlay';
  // The dropdown is shown for the overlay always; for inline only while the box has focus.
  const dropdownOpen = isOverlay || focused;

  const close = useCallback(() => {
    setFocused(false);
    inputRef.current?.blur();
    onClose?.();
  }, [onClose]);

  // Focus the input on mount for the overlay (the user just tapped the magnifier to get here).
  useEffect(() => {
    if (!isOverlay) return undefined;
    const id = window.setTimeout(() => inputRef.current?.focus(), 50);
    return () => window.clearTimeout(id);
  }, [isOverlay]);

  // Escape closes (both variants). For inline this only matters while focused.
  useEffect(() => {
    if (!dropdownOpen) return undefined;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [dropdownOpen, close]);

  // Inline: dismiss the dropdown on a click anywhere outside the box+dropdown wrapper. (The overlay
  // uses its own backdrop for this.)
  useEffect(() => {
    if (isOverlay || !focused) return undefined;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setFocused(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [isOverlay, focused]);

  // Overlay only: lock body scroll while the full-width bar is up.
  useEffect(() => {
    if (!isOverlay) return undefined;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, [isOverlay]);

  const propertyCount = properties.length;
  const tradeCount = tradeResults.length;

  const goProperty = (p: Property) => {
    close();
    navigate(`/properties/${encodeURIComponent(p.id)}`);
  };

  const goTrade = (r: TradeSearchResult) => {
    close();
    // The buyer lands on the seller's storefront (the same target the in-feed card + share link use
    // — there is no standalone listing detail route). encodeURIComponent guards a reserved-char id.
    navigate(`/trade/sellers/${encodeURIComponent(r.seller_id)}`);
  };

  const anyLoading = propsLoading || (canSearchTrade && tradeLoading);
  const anyError = propsError || (canSearchTrade && tradeError);

  // The dropdown body: one list, two sections (Homes + Shops & Products), shown together. Sections
  // render as soon as their half arrives (the two backends resolve independently).
  const dropdownBody = useMemo(() => {
    if (!hasQuery) {
      return (
        <div className="navbar-search__hint">
          <Icon name="search" size={22} />
          <p>Find homes, shops and products near you.</p>
        </div>
      );
    }
    if (anyError && propertyCount === 0 && tradeCount === 0) {
      return (
        <div className="navbar-search__hint navbar-search__hint--error">
          <Icon name="alertTriangle" size={22} />
          <p>Something went wrong. Please try again.</p>
        </div>
      );
    }
    if (anyLoading && propertyCount === 0 && tradeCount === 0) {
      return <div className="navbar-search__hint"><p>Searching…</p></div>;
    }
    if (propertyCount === 0 && tradeCount === 0) {
      return (
        <div className="navbar-search__hint">
          <p>No matches for “{debouncedQuery.trim()}”.</p>
        </div>
      );
    }

    return (
      <>
        {propertyCount > 0 && (
          <section className="navbar-search__section">
            <p className="navbar-search__section-title">Homes · {propertyCount}</p>
            <ul className="navbar-search__list">
              {properties.map((p) => <PropertyRow key={p.id} property={p} onSelect={goProperty} />)}
            </ul>
          </section>
        )}
        {canSearchTrade && tradeCount > 0 && (
          <section className="navbar-search__section">
            <p className="navbar-search__section-title">Shops &amp; Products · {tradeCount}</p>
            <ul className="navbar-search__list">
              {tradeResults.map((r) => <TradeRow key={r.listing_id} result={r} onSelect={goTrade} />)}
            </ul>
          </section>
        )}
      </>
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasQuery, anyError, anyLoading, propertyCount, tradeCount, properties, tradeResults,
      canSearchTrade, debouncedQuery]);

  // The box (input + icons) — shared by both variants.
  const box = (
    <div className="navbar-search__box">
      <span className="navbar-search__box-icon"><Icon name="search" size={18} /></span>
      <input
        ref={inputRef}
        type="text"
        className="navbar-search__input"
        placeholder={PLACEHOLDER}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => setFocused(true)}
        aria-label="Search query"
        autoComplete="off"
      />
      {query && (
        <button
          type="button"
          className="navbar-search__clear"
          // Keep focus so the dropdown stays open after clearing (mousedown fires before blur).
          onMouseDown={(e) => { e.preventDefault(); setQuery(''); inputRef.current?.focus(); }}
          aria-label="Clear search"
        >
          <Icon name="x" size={16} />
        </button>
      )}
    </div>
  );

  if (isOverlay) {
    return createPortal(
      <>
        <div className="navbar-search__backdrop" onClick={close} />
        <div className="navbar-search navbar-search--overlay" role="dialog" aria-label="Search">
          <div className="navbar-search__overlay-head">
            {box}
            <button
              type="button"
              className="navbar-search__overlay-close"
              onClick={close}
              aria-label="Close search"
            >
              <Icon name="x" size={20} />
            </button>
          </div>
          <div className="navbar-search__dropdown navbar-search__dropdown--overlay">{dropdownBody}</div>
        </div>
      </>,
      document.body,
    );
  }

  // Inline (desktop).
  return (
    <div className="navbar-search navbar-search--inline" ref={wrapRef}>
      {box}
      {dropdownOpen && <div className="navbar-search__dropdown">{dropdownBody}</div>}
    </div>
  );
};

// ----------------------------- rows -----------------------------

const PropertyRow: React.FC<{ property: Property; onSelect: (p: Property) => void }> = ({
  property, onSelect,
}) => {
  const image = property.main_image ?? property.images?.[0];
  const imgSrc = resolveMediaUrl(image?.thumbnail_url || image?.url);
  const place = property.address?.location_name || property.location_name;
  return (
    <li>
      {/* mousedown-select: fire before the input's blur so the click isn't swallowed by the
          outside-click dismiss on the inline variant. */}
      <button
        type="button"
        className="navbar-search__row"
        onMouseDown={(e) => { e.preventDefault(); onSelect(property); }}
      >
        <span className="navbar-search__thumb">
          {imgSrc
            ? <img src={imgSrc} alt="" loading="lazy" />
            : <Icon name="home" size={20} />}
        </span>
        <span className="navbar-search__row-body">
          <span className="navbar-search__row-title">{property.title}</span>
          <span className="navbar-search__row-meta">
            <span className="navbar-search__row-price">
              {formatPrice(property.price, property.currency)}
            </span>
            {place && <span className="navbar-search__row-place">{place}</span>}
          </span>
        </span>
        <Icon name="chevronRight" size={16} className="navbar-search__row-chevron" />
      </button>
    </li>
  );
};

const TradeRow: React.FC<{ result: TradeSearchResult; onSelect: (r: TradeSearchResult) => void }> = ({
  result, onSelect,
}) => {
  const imgSrc = resolveMediaUrl(result.image_url);
  const catLabel = categoryLabel(result.shop_category);
  return (
    <li>
      <button
        type="button"
        className="navbar-search__row"
        onMouseDown={(e) => { e.preventDefault(); onSelect(result); }}
      >
        <span className="navbar-search__thumb">
          {imgSrc
            ? <img src={imgSrc} alt="" loading="lazy" />
            : <Icon name="trade" size={20} />}
        </span>
        <span className="navbar-search__row-body">
          <span className="navbar-search__row-title">{result.title}</span>
          <span className="navbar-search__row-meta">
            <span className="navbar-search__row-price">
              {formatTradePrice(result.price_cents, result.currency)}
            </span>
            {result.shop_name && (
              <span className="navbar-search__row-place">{result.shop_name}</span>
            )}
            {catLabel && (
              <span
                className="navbar-search__row-cat"
                style={{ color: categoryColor(result.shop_category) }}
              >
                {catLabel}
              </span>
            )}
          </span>
        </span>
        <Icon name="chevronRight" size={16} className="navbar-search__row-chevron" />
      </button>
    </li>
  );
};

export default NavbarSearch;
