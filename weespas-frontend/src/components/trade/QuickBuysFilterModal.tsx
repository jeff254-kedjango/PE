// QuickBuysFilterModal — the §8 Quick Buys filter popover (search / price / category / radius).
//
// LOCALIZED popover (NOT a full-page modal): it renders anchored inside the Quick Buys section
// (position:absolute under the filter button), so opening it dims/blocks only that card, never the
// whole page — matching the shop hovercard's in-context popover feel. Outside-click + Escape close
// it (both owned by the parent QuickBuys, which also owns the trigger, so the trigger can toggle
// without the close-then-reopen race).
//
// Two KINDS of control live here, deliberately split:
//   * SEARCH (client-side, live): a text field that filters the already-fetched slate by title in
//     the parent — NO refetch, no loading spinner, effects localized to this card (the spec's #4b).
//     Controlled by the parent via `search` / `onSearchChange`; applied on every keystroke.
//   * price / category / radius (server-side): a controlled editor seeded from the applied filters
//     on open, lifted to the parent only on "Apply" (so a half-edited filter never refetches). The
//     server validates + clamps (price ≥ 0, unknown categories dropped, radius clamped to the cap).
import React, { useCallback, useEffect, useState } from 'react';
import Icon from '../ui/Icon';
import { CATEGORY_META } from '../../utils/categories';
import type { QuickBuysFilters } from '../../api/commerce';
import './QuickBuysFilterModal.css';

interface QuickBuysFilterModalProps {
  isOpen: boolean;
  onClose: () => void;
  filters: QuickBuysFilters;
  onApply: (filters: QuickBuysFilters) => void;
  /** Live client-side title search (the parent filters the fetched slate — no refetch). */
  search: string;
  onSearchChange: (value: string) => void;
}

// Radius presets in km → metres (label, value). "Immediate" is the default 5 km near radius.
const RADIUS_PRESETS: { label: string; km: number }[] = [
  { label: '2 km', km: 2 },
  { label: '5 km', km: 5 },
  { label: '10 km', km: 10 },
  { label: '20 km', km: 20 },
];

const CATEGORY_ENTRIES = Object.entries(CATEGORY_META); // [slug, {label, colorVar}]

const QuickBuysFilterModal: React.FC<QuickBuysFilterModalProps> = ({
  isOpen, onClose, filters, onApply, search, onSearchChange,
}) => {
  const [minPrice, setMinPrice] = useState<string>('');
  const [maxPrice, setMaxPrice] = useState<string>('');
  const [categories, setCategories] = useState<string[]>([]);
  const [radiusKm, setRadiusKm] = useState<number | null>(null);

  // Seed local editor state from the applied filters whenever the sheet opens.
  useEffect(() => {
    if (!isOpen) return;
    setMinPrice(filters.minPriceCents != null ? String(filters.minPriceCents / 100) : '');
    setMaxPrice(filters.maxPriceCents != null ? String(filters.maxPriceCents / 100) : '');
    setCategories(filters.categories ?? []);
    setRadiusKm(filters.radiusM != null ? filters.radiusM / 1000 : null);
  }, [isOpen, filters]);

  const toggleCategory = useCallback((slug: string) => {
    setCategories((prev) => (prev.includes(slug) ? prev.filter((s) => s !== slug) : [...prev, slug]));
  }, []);

  const toMajorInt = (v: string): number | null => {
    const n = Number(v);
    return v.trim() !== '' && Number.isFinite(n) && n >= 0 ? Math.round(n) : null;
  };

  const handleApply = () => {
    const minMajor = toMajorInt(minPrice);
    const maxMajor = toMajorInt(maxPrice);
    onApply({
      // Prices are entered in major units (KES) → cents for the wire.
      minPriceCents: minMajor != null ? minMajor * 100 : null,
      maxPriceCents: maxMajor != null ? maxMajor * 100 : null,
      categories,
      radiusM: radiusKm != null ? radiusKm * 1000 : null,
    });
    onClose();
  };

  // "Clear" resets BOTH the server-filter editor and the live search (a full reset of the card).
  const handleClear = () => {
    setMinPrice(''); setMaxPrice(''); setCategories([]); setRadiusKm(null);
    onSearchChange('');
  };

  const activeCount =
    (minPrice.trim() ? 1 : 0) + (maxPrice.trim() ? 1 : 0) + categories.length + (radiusKm != null ? 1 : 0);

  if (!isOpen) return null;

  return (
    <div
      className="qb-popover"
      role="dialog"
      aria-modal="false"
      aria-label="Filter Quick Buys"
      data-testid="quick-buys-filter-modal"
    >
      <header className="qb-popover__header">
        <div className="qb-popover__header-left">
          <Icon name="sliders" size={16} />
          <h2>Filter Quick Buys</h2>
        </div>
        <button type="button" className="qb-popover__close" onClick={onClose} aria-label="Close">
          <Icon name="x" size={18} />
        </button>
      </header>

      <div className="qb-popover__body">
        {/* ── Live search (client-side; filters the fetched slate by title, no refetch) ── */}
        <section className="qb-section">
          <div className="qb-search">
            <Icon name="search" size={15} className="qb-search__icon" />
            <input
              type="search"
              className="qb-search__input"
              placeholder="Search these products…"
              value={search}
              onChange={(e) => onSearchChange(e.target.value)}
              aria-label="Search Quick Buys products"
              data-testid="quick-buys-search"
            />
            {search && (
              <button
                type="button"
                className="qb-search__clear"
                onClick={() => onSearchChange('')}
                aria-label="Clear search"
              >
                <Icon name="x" size={14} />
              </button>
            )}
          </div>
        </section>

        {/* ── Price ── */}
        <section className="qb-section">
          <h3 className="qb-section__title">Price (KES)</h3>
          <div className="qb-row">
            <div className="qb-field">
              <label htmlFor="qb-min">Min</label>
              <input id="qb-min" type="number" min={0} placeholder="0"
                value={minPrice} onChange={(e) => setMinPrice(e.target.value)} />
            </div>
            <div className="qb-field">
              <label htmlFor="qb-max">Max</label>
              <input id="qb-max" type="number" min={0} placeholder="Any"
                value={maxPrice} onChange={(e) => setMaxPrice(e.target.value)} />
            </div>
          </div>
        </section>

        {/* ── Category ── */}
        <section className="qb-section">
          <h3 className="qb-section__title">Category</h3>
          <div className="qb-chips">
            {CATEGORY_ENTRIES.map(([slug, meta]) => (
              <button
                key={slug}
                type="button"
                className={`qb-chip ${categories.includes(slug) ? 'active' : ''}`}
                onClick={() => toggleCategory(slug)}
                aria-pressed={categories.includes(slug)}
              >
                {meta.label}
              </button>
            ))}
          </div>
        </section>

        {/* ── Location radius ── */}
        <section className="qb-section">
          <h3 className="qb-section__title">
            <Icon name="mapPin" size={16} /> Within
          </h3>
          <div className="qb-chips">
            {RADIUS_PRESETS.map((r) => (
              <button
                key={r.km}
                type="button"
                className={`qb-chip ${radiusKm === r.km ? 'active' : ''}`}
                onClick={() => setRadiusKm((prev) => (prev === r.km ? null : r.km))}
                aria-pressed={radiusKm === r.km}
              >
                {r.label}
              </button>
            ))}
          </div>
        </section>
      </div>

      <footer className="qb-popover__footer">
        <button type="button" className="qb-popover__clear" onClick={handleClear}>Clear</button>
        <button type="button" className="qb-popover__apply" onClick={handleApply}>
          Apply
          {activeCount > 0 && <span className="qb-popover__badge">{activeCount}</span>}
        </button>
      </footer>
    </div>
  );
};

export default QuickBuysFilterModal;
