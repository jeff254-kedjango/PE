import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { PropertyFilterParams } from '../types/propertyApi';

const defaultFilters: PropertyFilterParams = {
  radius: 10,
  category: 'all',
  sort_by: 'created_at',
  sort_order: 'desc',
};

// Phase 8 — read the authed user's saved search defaults directly from
// the localStorage mirror (AuthContext writes it; useMe re-hydrates it).
// Synchronous read is intentional: this runs once inside the useState
// initializer, before the first paint, so the home page never flickers
// from the global default to the user's preferred radius.
type UserDefaults = {
  default_radius_km?: number | null;
  preferred_listing_type?: 'rent' | 'sale' | null;
};

function readUserDefaults(): UserDefaults {
  try {
    const raw = localStorage.getItem('weespas_user');
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return {
      default_radius_km: parsed?.default_radius_km ?? null,
      preferred_listing_type: parsed?.preferred_listing_type ?? null,
    };
  } catch {
    return {};
  }
}

/** Keys that map to number values */
const NUMBER_KEYS: (keyof PropertyFilterParams)[] = [
  'latitude', 'longitude', 'radius', 'min_price', 'max_price',
  'bedrooms', 'bathrooms', 'min_size', 'max_size', 'parking_spaces', 'year_built',
];

/** Keys that map to boolean values */
const BOOLEAN_KEYS: (keyof PropertyFilterParams)[] = ['engineer_certified', 'is_featured'];

/** Keys that map to string values */
const STRING_KEYS: (keyof PropertyFilterParams)[] = [
  'listing_type', 'category', 'city', 'county', 'location_name', 'sort_by', 'sort_order', 'query',
];

/** Internal keys not persisted to URL */
const SKIP_KEYS = new Set(['skip', 'limit']);

/** Every query key this hook owns. Anything else in the URL (e.g. `confirm` from a
    verification deep-link) is foreign — the sync effect must preserve it verbatim. */
const OWNED_KEYS = new Set<string>([
  ...(NUMBER_KEYS as string[]),
  ...(BOOLEAN_KEYS as string[]),
  ...(STRING_KEYS as string[]),
]);

function hasAnyFilterParam(params: URLSearchParams): boolean {
  let found = false;
  params.forEach((_v, key) => {
    if (!SKIP_KEYS.has(key)) found = true;
  });
  return found;
}

function parseFiltersFromParams(params: URLSearchParams): PropertyFilterParams {
  // Empty URL on a fresh visit — overlay the user's persisted defaults
  // onto the global defaults so "default_radius_km = 25" actually takes
  // effect without an extra render/effect cycle. Once the user touches
  // a filter (which writes to the URL), the URL wins on subsequent reads.
  const base: PropertyFilterParams = { ...defaultFilters };
  if (!hasAnyFilterParam(params)) {
    const u = readUserDefaults();
    if (typeof u.default_radius_km === 'number' && u.default_radius_km > 0) {
      base.radius = u.default_radius_km;
    }
    if (u.preferred_listing_type === 'rent' || u.preferred_listing_type === 'sale') {
      base.listing_type = u.preferred_listing_type;
    }
  }
  const filters: PropertyFilterParams = base;

  for (const key of NUMBER_KEYS) {
    const val = params.get(key);
    if (val !== null && val !== '') {
      const n = Number(val);
      if (!isNaN(n)) (filters as any)[key] = n;
    }
  }

  for (const key of BOOLEAN_KEYS) {
    const val = params.get(key);
    if (val === '1' || val === 'true') (filters as any)[key] = true;
  }

  for (const key of STRING_KEYS) {
    const val = params.get(key);
    if (val !== null && val !== '') (filters as any)[key] = val;
  }

  return filters;
}

function filtersToParams(filters: PropertyFilterParams): Record<string, string> {
  const out: Record<string, string> = {};

  for (const [key, value] of Object.entries(filters)) {
    if (SKIP_KEYS.has(key)) continue;
    if (value === undefined || value === null || value === '') continue;

    // Skip defaults to keep URL clean
    if (key === 'radius' && value === 10) continue;
    if (key === 'category' && value === 'all') continue;
    if (key === 'sort_by' && value === 'created_at') continue;
    if (key === 'sort_order' && value === 'desc') continue;

    if (typeof value === 'boolean') {
      if (value) out[key] = '1';
    } else {
      out[key] = String(value);
    }
  }

  return out;
}

export function useFilterParams() {
  const [searchParams, setSearchParams] = useSearchParams();

  // Initialize from URL on first render
  const [filters, setFilters] = useState<PropertyFilterParams>(() =>
    parseFiltersFromParams(searchParams)
  );

  // Detect if URL had search params on mount (meaning a shared link)
  const [searchApplied, setSearchApplied] = useState(() => {
    // If there are any filter-related params in URL, auto-apply search
    let hasParams = false;
    searchParams.forEach((_val, key) => {
      if (!SKIP_KEYS.has(key) && key !== 'sort_by' && key !== 'sort_order') {
        hasParams = true;
      }
    });
    return hasParams;
  });

  // Sync filters → URL params. This effect runs on every render where `filters` or the
  // router's `setSearchParams` identity changes — including the initial mount, StrictMode's
  // double-invoke, and any cross-route navigation. So it must be a no-op unless the FILTER
  // keys actually changed, and it must never disturb anything else in the URL:
  //   1. Merge into the CURRENT params (read live, not from the mount-time `searchParams`
  //      snapshot) so foreign keys — e.g. `?confirm=1` from a verification deep-link, or the
  //      pathname a catch-all `<Navigate>` just set — survive untouched.
  //   2. Bail when the owned keys are already in sync, so we don't fire a redundant
  //      `setSearchParams` that would re-assert the URL and clobber an in-flight redirect.
  // This replaces an `isInitialMount` ref guard that StrictMode defeated (the double mount
  // consumed the guard, then the second run wiped the query string).
  // NOTE: react-router's `setSearchParams` ALWAYS calls `navigate("?" + params)` — even when
  // the updater returns an unchanged value — which re-asserts the current pathname. So we must
  // not call it at all unless an owned key truly differs; otherwise the re-navigation would
  // clobber an in-flight redirect (a catch-all `<Navigate>`) or a foreign query key.
  useEffect(() => {
    const desired = filtersToParams(filters);
    const next = new URLSearchParams(searchParams);
    let changed = false;
    const ownedInUrl: string[] = [];
    next.forEach((_v, key) => { if (OWNED_KEYS.has(key)) ownedInUrl.push(key); });
    for (const key of ownedInUrl) {
      if (!(key in desired)) {
        next.delete(key);
        changed = true;
      }
    }
    for (const [key, value] of Object.entries(desired)) {
      if (next.get(key) !== value) {
        next.set(key, value);
        changed = true;
      }
    }
    if (changed) setSearchParams(next, { replace: true });
  }, [filters, searchParams, setSearchParams]);

  const handleFilterChange = useCallback((newFilters: Partial<PropertyFilterParams>) => {
    setFilters((prev) => ({ ...prev, ...newFilters }));
  }, []);

  return {
    filters,
    setFilters,
    searchApplied,
    setSearchApplied,
    handleFilterChange,
  };
}
