// src/components/admin/FeaturedManager.tsx
//
// Admin panel for the FREE "featured listings" promotion (the home-carousel ad slot).
// Featuring is editorial, not paid: an admin elevates a trustworthy listing into the
// carousel, optionally for a fixed duration. The carousel itself ranks featured
// listings by trust (engineer-certified / verified agent / InSAR-monitored), so this
// panel only decides WHICH listings are eligible and for how long.
//
// Two halves:
//   1. Active promotions — list current featured listings with an expiry countdown and
//      an Unfeature button.
//   2. Add a promotion — search any listing, then Feature it for 7 / 14 / 30 days or
//      with no expiry.
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useToast } from '../../context/ToastContext';
import Icon from '../ui/Icon';
import { listFeaturedProperties, setPropertyFeatured } from '../../api/admin';
import { filterProperties } from '../../api/properties';
import type { Property } from '../../types/propertyApi';

const DURATION_OPTIONS: { label: string; days: number | null }[] = [
  { label: '7 days', days: 7 },
  { label: '14 days', days: 14 },
  { label: '30 days', days: 30 },
  { label: 'No expiry', days: null },
];

// One page of active promotions in the admin panel (matches the backend default).
const PAGE_SIZE = 10;

/** Human countdown for a promotion's expiry. */
function expiryLabel(iso?: string | null): string {
  if (!iso) return 'No expiry';
  const ms = new Date(iso).getTime() - Date.now();
  if (Number.isNaN(ms)) return 'No expiry';
  if (ms <= 0) return 'Expired';
  const days = Math.floor(ms / 86_400_000);
  if (days >= 1) return `Expires in ${days} day${days === 1 ? '' : 's'}`;
  const hours = Math.max(1, Math.floor(ms / 3_600_000));
  return `Expires in ${hours} hour${hours === 1 ? '' : 's'}`;
}

const TrustChips: React.FC<{ p: Property }> = ({ p }) => (
  <span className="featured-chips">
    {p.is_engineer_certified && (
      <span className="featured-chip featured-chip--trust" title="Engineer-certified">
        <Icon name="verified" size={12} /> Certified
      </span>
    )}
  </span>
);

const FeaturedManager: React.FC<{ token: string }> = ({ token }) => {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [processing, setProcessing] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [submittedQuery, setSubmittedQuery] = useState('');
  // Zero-based page index for the active-promotions list.
  const [page, setPage] = useState(0);
  // Per-listing chosen duration (defaults to 30 days) for the Feature action.
  const [durationByListing, setDurationByListing] = useState<Record<string, number | null>>({});

  const {
    data: active,
    isLoading: activeLoading,
    isError: activeError,
  } = useQuery({
    // Page is part of the key so each page caches separately; placeholderData keeps
    // the previous page visible (no empty flash) while the next one loads.
    queryKey: ['featuredProperties', page],
    queryFn: () => listFeaturedProperties(token, { skip: page * PAGE_SIZE, limit: PAGE_SIZE }),
    placeholderData: (prev) => prev,
  });

  const total = active?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const {
    data: results,
    isLoading: searchLoading,
    isFetching: searchFetching,
  } = useQuery({
    queryKey: ['featuredSearch', submittedQuery],
    queryFn: () => filterProperties({ query: submittedQuery, limit: 20 }),
    enabled: submittedQuery.length > 0,
  });

  const activeIds = useMemo(
    () => new Set((active?.items ?? []).map((p) => p.id)),
    [active],
  );

  const refresh = useCallback(() => {
    // Both the admin list and the public home carousel. Prefix match invalidates
    // every cached page of ['featuredProperties', n].
    queryClient.invalidateQueries({ queryKey: ['featuredProperties'] });
    queryClient.invalidateQueries({ queryKey: ['featured'] });
  }, [queryClient]);

  // If unfeaturing empties the last page, step back so we never strand the admin
  // on a blank page past the end.
  useEffect(() => {
    if (page > 0 && page > pageCount - 1) setPage(pageCount - 1);
  }, [page, pageCount]);

  const onFeature = useCallback(
    async (p: Property, durationDays: number | null) => {
      setProcessing(p.id);
      try {
        await setPropertyFeatured(token, p.id, {
          is_featured: true,
          duration_days: durationDays,
        });
        toast.success(`"${p.title}" is now featured.`);
        refresh();
      } catch {
        toast.error('Could not feature this listing. Please try again.');
      } finally {
        setProcessing(null);
      }
    },
    [token, toast, refresh],
  );

  const onUnfeature = useCallback(
    async (p: Property) => {
      setProcessing(p.id);
      try {
        await setPropertyFeatured(token, p.id, { is_featured: false });
        toast.success(`"${p.title}" is no longer featured.`);
        refresh();
      } catch {
        toast.error('Could not unfeature this listing. Please try again.');
      } finally {
        setProcessing(null);
      }
    },
    [token, toast, refresh],
  );

  const onSubmitSearch = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    setSubmittedQuery(search.trim());
  }, [search]);

  return (
    <section className="admin-section">
      <div className="admin-section__header">
        <div>
          <h3 className="admin-section__title">Featured Listings</h3>
          <p className="admin-section__hint">
            Promote trustworthy listings into the home carousel (free). The carousel
            leads with engineer-certified and verified listings automatically.
          </p>
        </div>
      </div>

      {/* ---- Active promotions ---- */}
      <div className="featured-block">
        <h4 className="featured-block__title">
          Active promotions{active ? ` (${active.total})` : ''}
        </h4>
        {activeLoading && <p className="featured-muted">Loading…</p>}
        {activeError && <p className="featured-muted">Could not load featured listings.</p>}
        {active && active.items.length === 0 && (
          <p className="featured-muted">No active promotions. Feature a listing below.</p>
        )}
        <ul className="featured-list">
          {active?.items.map((p) => (
            <li key={p.id} className="featured-row">
              <div className="featured-row__main">
                <span className="featured-row__title">{p.title}</span>
                <span className="featured-row__meta">
                  {p.agent_name ?? 'Unknown agent'} · {p.location_name ?? '—'}
                  <TrustChips p={p} />
                </span>
                <span className="featured-row__expiry">{expiryLabel(p.featured_expires_at)}</span>
              </div>
              <button
                className="stats-action-btn"
                disabled={processing === p.id}
                onClick={() => onUnfeature(p)}
              >
                {processing === p.id ? 'Working…' : 'Unfeature'}
              </button>
            </li>
          ))}
        </ul>

        {/* Pagination — 10 per page, mirrors the backend. Hidden when it all fits. */}
        {total > PAGE_SIZE && (
          <div className="featured-pager">
            <button
              type="button"
              className="stats-action-btn"
              disabled={page <= 0}
              onClick={() => setPage((n) => Math.max(0, n - 1))}
            >
              <Icon name="chevronLeft" size={14} /> Prev
            </button>
            <span className="featured-pager__label">
              Page {page + 1} of {pageCount}
            </span>
            <button
              type="button"
              className="stats-action-btn"
              disabled={page >= pageCount - 1}
              onClick={() => setPage((n) => Math.min(pageCount - 1, n + 1))}
            >
              Next <Icon name="chevronRight" size={14} />
            </button>
          </div>
        )}
      </div>

      {/* ---- Add a promotion ---- */}
      <div className="featured-block">
        <h4 className="featured-block__title">Add a promotion</h4>
        <form className="featured-search" onSubmit={onSubmitSearch}>
          <input
            type="text"
            className="featured-search__input"
            placeholder="Search listings by title or location…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search listings to feature"
          />
          <button className="stats-action-btn stats-action-btn--primary" type="submit">
            <Icon name="search" size={16} /> Search
          </button>
        </form>

        {submittedQuery && (searchLoading || searchFetching) && (
          <p className="featured-muted">Searching…</p>
        )}
        {submittedQuery && results && results.items.length === 0 && (
          <p className="featured-muted">No listings match “{submittedQuery}”.</p>
        )}
        <ul className="featured-list">
          {results?.items
            .filter((p) => !activeIds.has(p.id))
            .map((p) => {
              const chosen =
                p.id in durationByListing ? durationByListing[p.id] : 30;
              return (
                <li key={p.id} className="featured-row">
                  <div className="featured-row__main">
                    <span className="featured-row__title">{p.title}</span>
                    <span className="featured-row__meta">
                      {p.agent_name ?? 'Unknown agent'} · {p.location_name ?? '—'}
                      <TrustChips p={p} />
                    </span>
                  </div>
                  <div className="featured-row__actions">
                    <select
                      className="featured-duration"
                      aria-label="Promotion duration"
                      value={chosen === null ? 'none' : String(chosen)}
                      onChange={(e) =>
                        setDurationByListing((m) => ({
                          ...m,
                          [p.id]: e.target.value === 'none' ? null : Number(e.target.value),
                        }))
                      }
                    >
                      {DURATION_OPTIONS.map((o) => (
                        <option key={o.label} value={o.days === null ? 'none' : String(o.days)}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                    <button
                      className="stats-action-btn stats-action-btn--primary"
                      disabled={processing === p.id}
                      onClick={() => onFeature(p, chosen)}
                    >
                      {processing === p.id ? 'Working…' : 'Feature'}
                    </button>
                  </div>
                </li>
              );
            })}
        </ul>
      </div>
    </section>
  );
};

export default FeaturedManager;
