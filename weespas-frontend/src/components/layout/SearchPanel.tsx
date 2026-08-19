import React, { useState, useCallback, useEffect, useRef } from 'react';
import { PropertyCategory, PropertyFilterParams, ListingType } from '../../types/propertyApi';
import { fetchCategories } from '../../api/properties';
import CustomSelect, { SelectOption } from '../ui/CustomSelect';
import Icon from '../ui/Icon';
import './SearchPanel.css';

interface SearchPanelProps {
  filters: PropertyFilterParams;
  onChange: (filters: Partial<PropertyFilterParams>) => void;
  onSearch: () => void;
  onUseLocation: () => void;
  onAdvancedSearch?: () => void;
}

const CATEGORY_LABELS: Record<PropertyCategory, string> = {
  house: 'House',
  apartment: 'Apartment',
  villa: 'Villa',
  studio: 'Studio',
  office: 'Office',
  land: 'Land',
  warehouse: 'Warehouse',
  shop: 'Shop',
  kiosk: 'Kiosk',
  container: 'Container',
  stall: 'Stall',
  commercial_space: 'Commercial',
  other: 'Other',
};

const FALLBACK_CATEGORIES: PropertyCategory[] = [
  'house', 'apartment', 'villa', 'studio', 'office',
  'land', 'warehouse', 'shop', 'kiosk', 'container', 'stall', 'commercial_space', 'other',
];

/**
 * SearchPanel — the property filter, presented as a filter-icon button that opens a LOCALIZED
 * popover (mirrors the Trade Quick Buys filter). It lives inline in the "Latest properties near you"
 * header (see App.tsx), so opening it drops a floating card under the button rather than occupying a
 * whole grid column. All the original filter features are preserved (sale/rent, radius, property
 * type, price range, search, advanced) — only the presentation changed from a sticky sidebar to this
 * popover. Outside-click + Escape close it (owned here, since this component owns its own trigger,
 * so toggling the button never races the close).
 *
 * "Search My Location" is promoted OUT of the popover form and sits to the LEFT of the Filters
 * trigger, so the header row reads: Title …… [Search My Location][Filters]. It keeps the crosshair
 * icon and the locating/located states; geolocation itself still comes from the onUseLocation prop.
 */
const SearchPanel: React.FC<SearchPanelProps> = ({ filters, onChange, onSearch, onUseLocation, onAdvancedSearch }) => {
  const [categories, setCategories] = useState<PropertyCategory[]>(FALLBACK_CATEGORIES);
  const [locating, setLocating] = useState(false);
  const [open, setOpen] = useState(false);
  // Anchors the popover + scopes outside-click detection to this control.
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    fetchCategories()
      .then((cats) => {
        if (!cancelled && cats.length > 0) setCategories(cats);
      })
      .catch(() => { /* use fallback */ });
    return () => { cancelled = true; };
  }, []);

  // Localized dismissal: outside-click (scoped to this control) + Escape. Owned here so the trigger
  // button can toggle without a close-then-reopen race.
  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const handleLocationClick = useCallback(() => {
    setLocating(true);
    onUseLocation();
    // Reset after a timeout in case geolocation takes a while
    setTimeout(() => setLocating(false), 5000);
  }, [onUseLocation]);

  const handleListingType = useCallback((type: ListingType) => {
    // Toggle: if already selected, deselect (clear); otherwise set
    onChange({ listing_type: filters.listing_type === type ? undefined : type });
  }, [filters.listing_type, onChange]);

  const handleNumberChange = useCallback(
    (field: keyof PropertyFilterParams) => (e: React.ChangeEvent<HTMLInputElement>) => {
      const val = e.target.value;
      onChange({ [field]: val === '' ? undefined : Number(val) });
    },
    [onChange]
  );

  const handleCategoryChange = useCallback(
    (value: string) => {
      onChange({ category: value as PropertyCategory | 'all' });
    },
    [onChange]
  );

  const categoryOptions: SelectOption[] = [
    { label: 'All Types', value: 'all' },
    ...categories.map((cat) => ({
      label: CATEGORY_LABELS[cat] ?? cat,
      value: cat,
    })),
  ];

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      onSearch();
      setOpen(false);
    },
    [onSearch]
  );

  // Count of active narrowings — drives the trigger badge and the "Clear all" affordance.
  const activeCount =
    (filters.listing_type ? 1 : 0) +
    (filters.category && filters.category !== 'all' ? 1 : 0) +
    (filters.bedrooms !== undefined ? 1 : 0) +
    (filters.bathrooms !== undefined ? 1 : 0) +
    (filters.min_price !== undefined ? 1 : 0) +
    (filters.max_price !== undefined ? 1 : 0) +
    (filters.latitude && filters.longitude ? 1 : 0);
  const hasActiveFilters = activeCount > 0;

  const located = Boolean(filters.latitude && filters.longitude);

  return (
    <div className="search-filter-bar">
      {/* "Search My Location" sits OUTSIDE the .search-filter anchor, to the LEFT of the Filters
          trigger, so the header row reads: Title …… [Search My Location][Filters].
          Being an outside sibling also means clicking it dismisses an open popover for free. */}
      <button
        type="button"
        className={`search-filter__locate ${locating ? 'locating' : ''} ${located ? 'located' : ''}`}
        onClick={handleLocationClick}
        disabled={locating}
        data-testid="search-locate"
      >
        <Icon name="crosshair" size={18} />
        <span className="search-filter__locate-label">
          {locating ? 'Locating…' : located ? 'Location set' : 'Search My Location'}
        </span>
      </button>

      <div className="search-filter" ref={rootRef}>
        <button
          type="button"
          className={`search-filter__trigger ${hasActiveFilters ? 'is-active' : ''}`}
          onClick={() => setOpen((v) => !v)}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label="Filter properties"
          data-testid="search-panel-open"
        >
          <Icon name="sliders" size={18} />
          <span className="search-filter__trigger-label">Filters</span>
          {hasActiveFilters && <span className="search-filter__trigger-badge">{activeCount}</span>}
        </button>

        {open && (
        <aside
          className="search-panel search-panel--popover"
          id="search-panel"
          role="dialog"
          aria-modal="false"
          aria-label="Filter properties"
        >
          <div className="search-panel__header">
            <span className="search-panel__title">Filters</span>
            <div className="search-panel__header-actions">
              {hasActiveFilters && (
                <button
                  type="button"
                  className="search-panel__clear"
                  onClick={() => onChange({
                    listing_type: undefined,
                    category: 'all',
                    bedrooms: undefined,
                    bathrooms: undefined,
                    min_price: undefined,
                    max_price: undefined,
                    latitude: undefined,
                    longitude: undefined,
                    radius: 10,
                  })}
                >
                  Clear all
                </button>
              )}
              {/* Advanced Filters — relocated here from the form footer to the header row. A gear
                  icon with a hover/focus tooltip; clicking opens the full advanced-search modal. */}
              {onAdvancedSearch && (
                <button
                  type="button"
                  className="search-panel__advanced"
                  onClick={() => { setOpen(false); onAdvancedSearch(); }}
                  aria-label="Advanced Filters"
                  data-testid="advanced-search-open"
                >
                  <Icon name="settings" size={18} />
                  <span className="search-panel__advanced-tip" role="tooltip">Advanced Filters</span>
                </button>
              )}
            </div>
          </div>

          <form className="search-panel__body" onSubmit={handleSubmit}>
            {/* Location moved OUT of this form to the header row (see .search-filter__locate above). */}
            {/* ── Sale / Rent Toggle ── */}
            <div className="search-panel__toggle">
              <button
                type="button"
                className={`toggle-pill ${filters.listing_type === 'rent' ? 'active' : ''}`}
                onClick={() => handleListingType('rent')}
              >
                For Rent
              </button>
              <button
                type="button"
                className={`toggle-pill ${filters.listing_type === 'sale' ? 'active' : ''}`}
                onClick={() => handleListingType('sale')}
              >
                For Sale
              </button>
            </div>

            {/* ── Radius ── */}
            <div className="field-group">
              <label>Radius (km)</label>
              <div className="radius-input-wrap">
                <input
                  type="range"
                  min={1}
                  max={100}
                  value={filters.radius ?? 10}
                  onChange={(e) => onChange({ radius: Number(e.target.value) })}
                  className="radius-slider"
                />
                <span className="radius-value">{filters.radius ?? 10} km</span>
              </div>
            </div>

            {/* ── Property Type ── */}
            <div className="field-group">
              <label>Property Type</label>
              <CustomSelect
                options={categoryOptions}
                value={filters.category ?? 'all'}
                onChange={handleCategoryChange}
                placeholder="All Types"
              />
            </div>

            {/* ── Price Range ── */}
            <div className="field-group">
              <label>Price Range (KES)</label>
              <div className="price-row">
                <input
                  type="number"
                  min={0}
                  value={filters.min_price ?? ''}
                  onChange={handleNumberChange('min_price')}
                  placeholder="Min"
                />
                <span className="price-divider">—</span>
                <input
                  type="number"
                  min={0}
                  value={filters.max_price ?? ''}
                  onChange={handleNumberChange('max_price')}
                  placeholder="Max"
                />
              </div>
            </div>

            {/* ── Search Button ── */}
            <button type="submit" className="primary-button search-button">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              Search Properties
            </button>
            {/* Advanced Filters now lives in the header (gear icon) — see .search-panel__header. */}
          </form>
        </aside>
        )}
      </div>
    </div>
  );
};

export default SearchPanel;
