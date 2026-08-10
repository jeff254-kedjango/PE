// QuickBuys — the §8 Trade right-rail discovery grid: a 3×3 paged product grid with prev/next nav
// and a price/category/radius filter.
//
// Data: useQuickBuys → a composed near/interest MIX (4 near : 5 interest per page, see commerce
// services/quick_buys.py). The server returns the whole bounded slate; this component PAGES over it
// locally (no per-page refetch) in page_size windows. The nav chevrons (bottom-right, matching the
// ShopVideoStrip shelf's nav) step whole pages and disable at the bounds.
//
// The header carries the title on the left and a filter button on the far right (its icon opens a
// LOCALIZED popover anchored to this section — not a full-page modal). Server-side filters
// (price/category/radius) lift to local state on "Apply" → the hook refetches → paging resets. A
// live title SEARCH is client-side: it narrows the already-fetched slate here (no refetch), so its
// effect is localized to this card exactly (the spec's #4b).
//
// Chunk 1 (permanent columns): the section stays MOUNTED even with zero items and no active
// filter, so the right column keeps its width on load. Shimmering skeleton grid while the initial
// fetch is in flight; an honest "Nothing to grab here yet" placeholder once the server confirms
// empty. Filter button REMAINS VISIBLE in the empty state — filters here are server-side, so
// changing them (wider radius, different category) can genuinely surface items that the default
// slate doesn't include; hiding the button would remove a real escape hatch (rule 4 does NOT apply
// — the affordance is not dead).
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Icon from '../ui/Icon';
import QuickBuyCard from './QuickBuyCard';
import QuickBuysFilterModal from './QuickBuysFilterModal';
import { useQuickBuys } from '../../hooks/useQuickBuys';
import type { CommerceSession, QuickBuysFilters } from '../../api/commerce';
import './QuickBuys.css';

interface QuickBuysProps {
  session: CommerceSession | null;
  lat: number;
  lng: number;
  onSelectSeller: (sellerId: string) => void;
}

const QuickBuys: React.FC<QuickBuysProps> = ({ session, lat, lng, onSelectSeller }) => {
  const [filters, setFilters] = useState<QuickBuysFilters>({});
  const [search, setSearch] = useState('');
  const [filterOpen, setFilterOpen] = useState(false);
  const [page, setPage] = useState(0);
  // Anchors the localized popover + scopes outside-click detection to this section.
  const sectionRef = useRef<HTMLDivElement>(null);

  const { data, isLoading } = useQuickBuys({ session, lat, lng, filters });
  const rawItems = data?.items ?? [];
  const pageSize = data?.page_size ?? 9;

  // Client-side title search over the fetched slate — case-insensitive substring, no refetch. An
  // empty/whitespace query is a no-op (shows the full slate). Memoised so paging math is stable.
  const items = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rawItems;
    return rawItems.filter((it) => it.title.toLowerCase().includes(q));
  }, [rawItems, search]);

  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));

  // Clamp the current page if the slate shrank (e.g. a filter/search narrowed it) — never show a
  // blank page past the end. Derived, so it self-corrects on every render without an effect.
  const safePage = Math.min(page, pageCount - 1);
  const pageItems = useMemo(
    () => items.slice(safePage * pageSize, safePage * pageSize + pageSize),
    [items, safePage, pageSize],
  );

  const applyFilters = (next: QuickBuysFilters) => {
    setFilters(next);
    setPage(0); // a new filter set starts from the first page
  };

  // A live search change narrows the shown slate; reset paging so results start at page 0.
  const changeSearch = useCallback((value: string) => {
    setSearch(value);
    setPage(0);
  }, []);

  // Localized popover dismissal: outside-click (scoped to this section) + Escape. Owned here (not in
  // the popover) so the trigger button can toggle without a close-then-reopen race.
  useEffect(() => {
    if (!filterOpen) return;
    const onPointerDown = (e: PointerEvent) => {
      if (sectionRef.current && !sectionRef.current.contains(e.target as Node)) setFilterOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setFilterOpen(false); };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [filterOpen]);

  const anyFilterActive =
    filters.minPriceCents != null || filters.maxPriceCents != null ||
    (filters.categories?.length ?? 0) > 0 || filters.radiusM != null;
  // A live search is also an "active" narrowing — keep the section mounted (with a "no matches"
  // hint) when it empties the slate, so the user can see the result and clear it.
  const anyNarrowingActive = anyFilterActive || search.trim() !== '';

  // Chunk 1: stay MOUNTED unconditionally. Three visual states below:
  //   - items present  → real grid
  //   - items empty + a filter/search is active → "no matches — try widening" (unchanged)
  //   - items empty + no narrowing → skeleton grid (loading) OR authoritative empty placeholder
  const showSkeleton = isLoading && items.length === 0;
  const authoritativeEmpty = !isLoading && items.length === 0 && !anyNarrowingActive;

  return (
    <section className="quick-buys" aria-label="Quick Buys" ref={sectionRef}>
      <header className="quick-buys__header">
        <h3 className="quick-buys__title">Quick Buys</h3>
        {/* Relative anchor so the localized popover drops directly under the filter button (scoped
            to this section — never a full-page overlay). */}
        <div className="quick-buys__filter">
          <button
            type="button"
            className={`quick-buys__filter-btn ${anyNarrowingActive ? 'is-active' : ''}`}
            onClick={() => setFilterOpen((v) => !v)}
            aria-haspopup="dialog"
            aria-expanded={filterOpen}
            aria-label="Filter Quick Buys"
            data-testid="quick-buys-filter-open"
          >
            <Icon name="sliders" size={16} />
          </button>
          <QuickBuysFilterModal
            isOpen={filterOpen}
            onClose={() => setFilterOpen(false)}
            filters={filters}
            onApply={applyFilters}
            search={search}
            onSearchChange={changeSearch}
          />
        </div>
      </header>

      {showSkeleton ? (
        // Shimmering skeleton — nine 1:1 tiles matching the real 3×3 grid so the section holds its
        // vertical footprint before data lands. Uses the shared `.skeleton` class from
        // styles/animations.css; the global reduced-motion guard in styles/reset.css freezes it
        // automatically. aria-hidden: purely visual (no items to announce yet).
        <div
          className="quick-buys__grid quick-buys__grid--skeleton"
          data-testid="quick-buys-skeleton"
          aria-hidden="true"
        >
          {Array.from({ length: 9 }).map((_, i) => (
            <div key={i} className="skeleton quick-buys__tile-skeleton" />
          ))}
        </div>
      ) : authoritativeEmpty ? (
        // Authoritative empty — the server answered with zero and no filter is narrowing the slate.
        <p className="quick-buys__empty" data-testid="quick-buys-empty">
          Nothing to grab here yet — check back soon.
        </p>
      ) : items.length === 0 ? (
        // A filter or search is active but yields nothing — the user can widen or clear.
        <p className="quick-buys__empty">No matches — try a different search or widen your filters.</p>
      ) : (
        <div className="quick-buys__grid" data-testid="quick-buys-grid">
          {pageItems.map((item) => (
            <QuickBuyCard key={item.id} item={item} session={session} onSelectSeller={onSelectSeller} />
          ))}
        </div>
      )}

      {/* Prev/next nav — bottom-right, matching the ShopVideoStrip shelf's chevron nav. Steps whole
          pages; each button disables at its bound. Hidden when a single page fits everything. */}
      {pageCount > 1 && (
        <div className="quick-buys__nav">
          <span className="quick-buys__page" aria-live="polite">{safePage + 1} / {pageCount}</span>
          <button
            type="button"
            className="quick-buys__nav-btn"
            onClick={() => setPage(Math.max(0, safePage - 1))}
            disabled={safePage === 0}
            aria-label="Previous Quick Buys"
          >
            <Icon name="chevronLeft" size={18} />
          </button>
          <button
            type="button"
            className="quick-buys__nav-btn"
            onClick={() => setPage(Math.min(pageCount - 1, safePage + 1))}
            disabled={safePage >= pageCount - 1}
            aria-label="Next Quick Buys"
          >
            <Icon name="chevronRight" size={18} />
          </button>
        </div>
      )}
    </section>
  );
};

export default QuickBuys;
