import React, { useState, useCallback, useEffect, useMemo } from 'react';
import { Link, Navigate, useSearchParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { useAgentStats } from '../hooks/useAgentStats';
import { useAgentProperties } from '../hooks/useAgentProperties';
import { useAllProperties } from '../hooks/useAllProperties';
import { deleteProperty } from '../api/properties';
import Icon from '../components/ui/Icon';
import ListingTypeBadge from '../components/ui/ListingTypeBadge';
import ConfirmedShield from '../components/ui/ConfirmedShield';
import { useConfirmedListings } from '../hooks/useConfirmedListings';
import AddPropertyModal from '../components/ui/AddPropertyModal';
import EditPropertyModal from '../components/ui/EditPropertyModal';
import ConfirmDeleteDialog from '../components/ui/ConfirmDeleteDialog';
import UnifiedSearchPanel from '../components/ui/UnifiedSearchPanel';
import PageMeta from '../components/ui/PageMeta';
import AnalyticsSummaryStrip from '../components/analytics/AnalyticsSummaryStrip';
import CategoryInterestChart from '../components/analytics/CategoryInterestChart';
import PriceRangeChart from '../components/analytics/PriceRangeChart';
import HeatmapMap from '../components/analytics/HeatmapMap';
import TimeRangePicker from '../components/analytics/TimeRangePicker';
import AgentRankCard from '../components/analytics/AgentRankCard';
import ConversionFunnelCard from '../components/analytics/ConversionFunnelCard';
import ListingBenchmarkCell from '../components/analytics/ListingBenchmarkCell';
import { useListingBenchmarks } from '../hooks/useAnalytics';
import type { SinceWindow, ListingBenchmark } from '../types/analytics';
import { formatPrice, formatDate } from '../utils/format';
import { resolveMediaUrl } from '../utils/media';
import { hasRole, primaryRole } from '../utils/roles';
import type { Property } from '../types/propertyApi';
import './StatsPage.css';

const PAGE_SIZE = 10;

function getPropertyThumb(property: Property): string | null {
  const raw = property.main_image?.thumbnail_url
    || property.main_image?.url
    || property.images?.[0]?.thumbnail_url
    || property.images?.[0]?.url
    || null;
  return resolveMediaUrl(raw) ?? null;
}

const StatsPage: React.FC = () => {
  const { user, token, isAuthenticated } = useAuth();
  const [searchParams] = useSearchParams();
  const { toast } = useToast();
  const [currentPage, setCurrentPage] = useState(0);
  const [listingScope, setListingScope] = useState<'mine' | 'all'>('mine');
  const [analyticsScope, setAnalyticsScope] = useState<'mine' | 'global'>('mine');
  const [showAddModal, setShowAddModal] = useState(false);
  const [editingProperty, setEditingProperty] = useState<Property | null>(null);
  const [deletingProperty, setDeletingProperty] = useState<Property | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const [analyticsSince, setAnalyticsSince] = useState<SinceWindow>('30d');
  const queryClient = useQueryClient();

  const {
    data: stats,
    isLoading: statsLoading,
    isError: statsError,
  } = useAgentStats(token, analyticsScope);

  const {
    data: minePropertiesData,
    isLoading: mineLoading,
    isError: mineErrorRaw,
    error: mineErrorObj,
  } = useAgentProperties(token, { skip: currentPage * PAGE_SIZE, limit: PAGE_SIZE });

  const {
    data: allPropertiesData,
    isLoading: allLoading,
    isError: allError,
  } = useAllProperties(listingScope === 'all', {
    skip: currentPage * PAGE_SIZE,
    limit: PAGE_SIZE,
  });

  const propertiesData = listingScope === 'mine' ? minePropertiesData : allPropertiesData;
  const propsLoading = listingScope === 'mine' ? mineLoading : allLoading;
  const propsErrorRaw = listingScope === 'mine' ? mineErrorRaw : allError;
  const propsErrorObj = listingScope === 'mine' ? mineErrorObj : undefined;

  // A fresh agent who hasn't been linked to an Agent profile yet (or whose
  // profile is empty) gets a 4xx from /agents/me/properties. Treat that as
  // "zero listings" instead of a hard error so the page still loads.
  const propsErrorMessage = (propsErrorObj as Error | undefined)?.message ?? '';
  const isFreshAgentResponse =
    propsErrorRaw &&
    /4\d\d/.test(propsErrorMessage) &&
    /(No agent profile|Agent profile not found|No properties)/i.test(propsErrorMessage);
  const propsError = propsErrorRaw && !isFreshAgentResponse;

  const isAdmin = hasRole(user, 'admin');

  const showAgentCompare = isAuthenticated
    && !!user
    && hasRole(user, 'agent')
    && analyticsScope === 'mine'
    && listingScope === 'mine';

  const { data: benchmarks } = useListingBenchmarks(
    showAgentCompare ? token : null,
    analyticsSince,
  );

  const benchmarksById = useMemo<Record<string, ListingBenchmark>>(() => {
    if (!benchmarks) return {};
    const map: Record<string, ListingBenchmark> = {};
    for (const b of benchmarks) {
      map[b.property_id] = b;
    }
    return map;
  }, [benchmarks]);

  if (!isAuthenticated || !user || !(hasRole(user, 'agent') || hasRole(user, 'staff') || hasRole(user, 'admin'))) {
    return <Navigate to="/profile" replace />;
  }

  const properties = propertiesData?.items ?? [];
  const totalProperties = propertiesData?.total ?? 0;
  const totalPages = Math.ceil(totalProperties / PAGE_SIZE);

  // Which of the visible listings map to a building with a recorded on-the-ground
  // assessment → green shield. One batched call per page (no N+1); O(1) Set lookup.
  const visibleIds = useMemo(() => properties.map((p) => p.id), [properties]);
  const confirmedIds = useConfirmedListings(visibleIds);

  // If the current page becomes empty after a delete, go back one page
  useEffect(() => {
    if (!propsLoading && properties.length === 0 && currentPage > 0) {
      setCurrentPage((p) => p - 1);
    }
  }, [propsLoading, properties.length, currentPage]);

  const handleDeleteConfirm = useCallback(async () => {
    if (!deletingProperty || !token) return;
    setDeleting(true);
    try {
      await deleteProperty(token, deletingProperty.id);
      toast.success('Property deleted successfully');
      queryClient.invalidateQueries({ queryKey: ['agentProperties'] });
      queryClient.invalidateQueries({ queryKey: ['allProperties'] });
      queryClient.invalidateQueries({ queryKey: ['agentStats'] });
      setDeletingProperty(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to delete property';
      toast.error(message);
    } finally {
      setDeleting(false);
    }
  }, [deletingProperty, token, toast, queryClient]);

  // Donut chart data
  const saleCount = stats?.properties_for_sale ?? 0;
  const rentCount = stats?.properties_for_rent ?? 0;
  const listingTotal = saleCount + rentCount;
  const salePercent = listingTotal > 0 ? (saleCount / listingTotal) * 100 : 0;
  const donutStyle = listingTotal > 0
    ? { background: `conic-gradient(var(--color-sale-bg) 0% ${salePercent}%, var(--color-rent-bg) ${salePercent}% 100%)` }
    : undefined;

  // Bar chart data — top 5 properties by views
  const sortedByViews = [...properties]
    .sort((a, b) => (b.view_count ?? 0) - (a.view_count ?? 0))
    .slice(0, 5);
  const maxViews = sortedByViews.length > 0
    ? Math.max(...sortedByViews.map(p => p.view_count ?? 0), 1)
    : 1;

  // Multi-role users can request a specific dashboard view via ?view=staff|agent|admin.
  // Falls back to their highest-privilege role if the requested view isn't held.
  const requestedView = searchParams.get('view') as 'admin' | 'staff' | 'agent' | null;
  const dashboardRole = requestedView && hasRole(user, requestedView)
    ? requestedView
    : primaryRole(user);
  const dashboardTitle = dashboardRole === 'admin' ? 'Admin Dashboard'
    : dashboardRole === 'staff' ? 'Staff Dashboard'
    : 'Agent Dashboard';

  const statTiles = [
    { icon: 'grid' as const, label: 'Total Properties', value: stats?.total_properties, variant: 'primary' },
    { icon: 'check' as const, label: 'Active Listings', value: stats?.active_properties, variant: 'success' },
    { icon: 'barChart' as const, label: 'Total Views', value: stats?.total_views, variant: 'info' },
    { icon: 'verified' as const, label: 'Featured', value: stats?.featured_count, variant: 'warning' },
  ];

  return (
    <div className="stats-page">
      <PageMeta
        title={dashboardTitle}
        description="View your property listing performance, inquiries, and agent statistics on Weespas."
      />
      <div className="stats-container">
        {/* Back nav */}
        <Link to="/profile" className="stats-back">
          <Icon name="arrowLeft" size={18} />
          <span>Back to Profile</span>
        </Link>

        {/* Header */}
        <header className="stats-header">
          <div className="stats-header__top">
            <h1>{dashboardTitle}</h1>
            <span className="stats-header__role">
              {dashboardRole === 'admin' ? 'Admin' : dashboardRole === 'staff' ? 'Staff' : 'Agent'}
            </span>
          </div>
          <p className="stats-header__subtitle">
            {statsLoading
              ? 'Loading your dashboard...'
              : analyticsScope === 'global'
                ? 'Viewing platform-wide analytics across all listings'
                : stats
                  ? `Welcome back, ${stats.agent_name}`
                  : 'Your property management hub'}
          </p>
        </header>

        {/* Error Banner */}
        {statsError && (
          <div className="stats-error">
            <Icon name="alertTriangle" size={18} />
            <span>Failed to load dashboard data. Please try again later.</span>
          </div>
        )}

        {/* Stats Overview */}
        <section className="stats-overview">
          {statTiles.map((tile) => (
            <div
              key={tile.label}
              className={`stats-tile${statsLoading ? ' stats-tile--loading' : ''}`}
            >
              <div className={`stats-tile__icon stats-tile__icon--${tile.variant}`}>
                <Icon name={tile.icon} size={20} />
              </div>
              <span className="stats-tile__value">
                {statsLoading ? '\u00A0\u00A0' : statsError ? '--' : tile.value?.toLocaleString() ?? '0'}
              </span>
              <span className="stats-tile__label">{tile.label}</span>
            </div>
          ))}
        </section>

        {/* Quick Actions */}
        <section className="stats-actions">
          <button
            className="stats-action-btn stats-action-btn--primary"
            onClick={() => setShowAddModal(true)}
          >
            <Icon name="grid" size={16} />
            Add New Property
          </button>
          <button
            className="stats-action-btn stats-action-btn--secondary"
            onClick={() => setShowSearch(true)}
          >
            <Icon name="search" size={16} />
            Search
          </button>
          <div
            className="stats-scope-tabs stats-scope-tabs--analytics"
            role="tablist"
            aria-label="Analytics scope"
          >
            <button
              role="tab"
              aria-selected={analyticsScope === 'mine'}
              className={`stats-scope-tab${analyticsScope === 'mine' ? ' stats-scope-tab--active' : ''}`}
              onClick={() => setAnalyticsScope('mine')}
            >
              {`${user.name?.split(' ')[0] ?? 'My'} Analytics`}
            </button>
            <button
              role="tab"
              aria-selected={analyticsScope === 'global'}
              className={`stats-scope-tab${analyticsScope === 'global' ? ' stats-scope-tab--active' : ''}`}
              onClick={() => setAnalyticsScope('global')}
            >
              Global Analytics
            </button>
          </div>
        </section>

        {/* Charts */}
        <section className="stats-charts">
          {/* Donut Chart — Listing Type Breakdown */}
          <div className="stats-chart-card">
            <h3 className="stats-chart-card__title">Listing Breakdown</h3>
            <div className="stats-donut-wrapper">
              <div
                className={`stats-donut${listingTotal === 0 ? ' stats-donut--empty' : ''}`}
                style={donutStyle}
                role="img"
                aria-label={
                  listingTotal === 0
                    ? 'No active listings'
                    : `${saleCount} for sale, ${rentCount} for rent (${Math.round(salePercent)}% sale)`
                }
              >
                <div className="stats-donut__inner">
                  <span className="stats-donut__total">
                    {statsLoading ? '-' : stats?.active_properties ?? 0}
                  </span>
                  <span className="stats-donut__total-label">Active</span>
                </div>
              </div>
              <div className="stats-donut-legend">
                <div className="stats-donut-legend__item">
                  <span className="stats-donut-legend__dot stats-donut-legend__dot--sale" />
                  <span>For Sale</span>
                  <span className="stats-donut-legend__count">{saleCount}</span>
                </div>
                <div className="stats-donut-legend__item">
                  <span className="stats-donut-legend__dot stats-donut-legend__dot--rent" />
                  <span>For Rent</span>
                  <span className="stats-donut-legend__count">{rentCount}</span>
                </div>
                <div className="stats-donut-legend__item">
                  <span className="stats-donut-legend__dot stats-donut-legend__dot--inactive" />
                  <span>Inactive</span>
                  <span className="stats-donut-legend__count">{stats?.inactive_properties ?? 0}</span>
                </div>
                <div className="stats-donut-legend__item">
                  <span className="stats-donut-legend__dot stats-donut-legend__dot--certified" />
                  <span>Certified</span>
                  <span className="stats-donut-legend__count">{stats?.engineer_certified_count ?? 0}</span>
                </div>
              </div>
            </div>
          </div>

          {/* Bar Chart — Top Properties by Views */}
          <div className="stats-chart-card">
            <h3 className="stats-chart-card__title">Top Properties by Views</h3>
            {propsLoading ? (
              <div className="stats-chart-empty">Loading...</div>
            ) : sortedByViews.length === 0 || sortedByViews.every(p => (p.view_count ?? 0) === 0) ? (
              <div className="stats-chart-empty">No view data yet</div>
            ) : (
              <div className="stats-bar">
                {sortedByViews.map((property) => (
                  <div key={property.id} className="stats-bar__row">
                    <span className="stats-bar__label" title={property.title}>
                      {property.title}
                    </span>
                    <div className="stats-bar__track">
                      <div
                        className="stats-bar__fill"
                        style={{ width: `${((property.view_count ?? 0) / maxViews) * 100}%` }}
                      />
                    </div>
                    <span className="stats-bar__count">
                      {(property.view_count ?? 0).toLocaleString()}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        {/* How you compare — agent-self comparative views */}
        {showAgentCompare && (
          <section className="stats-compare">
            <header className="stats-compare__header">
              <h2>How you compare</h2>
              <TimeRangePicker value={analyticsSince} onChange={setAnalyticsSince} />
            </header>
            <div className="stats-compare__grid">
              <AgentRankCard token={token} since={analyticsSince} />
              <ConversionFunnelCard token={token} since={analyticsSince} />
            </div>
          </section>
        )}

        {/* Analytics — categories, prices */}
        <section className="stats-analytics">
          <header className="stats-analytics__header">
            <h2>Analytics</h2>
            <TimeRangePicker value={analyticsSince} onChange={setAnalyticsSince} />
          </header>
          
          <AnalyticsSummaryStrip token={token} since={analyticsSince} />
          <div className="stats-analytics__charts">
            <CategoryInterestChart token={token} since={analyticsSince} />
            <PriceRangeChart token={token} since={analyticsSince} />
          </div>
          <HeatmapMap token={token} since={analyticsSince} />
        </section>

        {/* Property Management */}
        <section className="stats-properties">
          <div className="stats-properties__header">
            <div className="stats-scope-tabs" role="tablist" aria-label="Property scope">
              <button
                role="tab"
                aria-selected={listingScope === 'mine'}
                className={`stats-scope-tab${listingScope === 'mine' ? ' stats-scope-tab--active' : ''}`}
                onClick={() => { setListingScope('mine'); setCurrentPage(0); }}
              >
                Your Listings
              </button>
              <button
                role="tab"
                aria-selected={listingScope === 'all'}
                className={`stats-scope-tab${listingScope === 'all' ? ' stats-scope-tab--active' : ''}`}
                onClick={() => { setListingScope('all'); setCurrentPage(0); }}
              >
                All Available Listings
              </button>
            </div>
            {!propsLoading && (
              <span className="stats-properties__count">{totalProperties}</span>
            )}
          </div>

          {propsLoading ? (
            <div className="stats-table-skeleton">
              {[1, 2, 3].map((i) => (
                <div key={i} className="stats-table-skeleton__row">
                  <div className="stats-table-skeleton__thumb" />
                  <div className="stats-table-skeleton__block" style={{ width: '40%' }} />
                  <div className="stats-table-skeleton__block" style={{ width: '15%' }} />
                  <div className="stats-table-skeleton__block" style={{ width: '10%' }} />
                </div>
              ))}
            </div>
          ) : propsError ? (
            <div className="stats-properties-error">
              <Icon name="alertTriangle" size={18} />
              <p>Failed to load properties. Please try again.</p>
            </div>
          ) : properties.length === 0 ? (
            <div className="stats-properties-empty">
              <div className="stats-properties-empty__icon">
                <Icon name="grid" size={40} />
              </div>
              <p className="stats-properties-empty__text">
                {listingScope === 'mine' ? 'Zero listings at the moment, add more listings' : 'No listings on the platform yet'}
              </p>
              {listingScope === 'mine' && (
                <button
                  className="stats-action-btn stats-action-btn--primary"
                  onClick={() => setShowAddModal(true)}
                >
                  <Icon name="grid" size={16} />
                  Add Your First Property
                </button>
              )}
            </div>
          ) : (
            <>
              {/* Desktop Table */}
              <table className="stats-table">
                <thead>
                  <tr>
                    <th></th>
                    <th>Title</th>
                    <th>Type</th>
                    <th>Status</th>
                    <th>Price</th>
                    <th>Views</th>
                    {showAgentCompare && <th>vs peers</th>}
                    <th>Listed</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {properties.map((property) => {
                    const thumb = getPropertyThumb(property);
                    const canManage = isAdmin || listingScope === 'mine';
                    return (
                      <tr key={property.id}>
                        <td>
                          {thumb ? (
                            <img
                              src={thumb}
                              alt={property.title}
                              className="stats-table-thumb"
                              loading="lazy"
                            />
                          ) : (
                            <div className="stats-table-thumb-placeholder">
                              <Icon name="mapPin" size={18} />
                            </div>
                          )}
                        </td>
                        <td>
                          <span className="stats-table__title">
                            <span className="stats-table__title-text">{property.title}</span>
                            {confirmedIds.has(property.id) && <ConfirmedShield size={15} />}
                          </span>
                        </td>
                        <td>
                          <ListingTypeBadge type={property.listing_type} />
                        </td>
                        <td>
                          <span className={`stats-status stats-status--${property.is_active !== false ? 'active' : 'inactive'}`}>
                            {property.is_active !== false ? 'Active' : 'Inactive'}
                          </span>
                        </td>
                        <td>{formatPrice(property.price, property.currency, property.listing_type)}</td>
                        <td className="stats-table__views">
                          {(property.view_count ?? 0).toLocaleString()}
                        </td>
                        {showAgentCompare && (
                          <td>
                            <ListingBenchmarkCell benchmark={benchmarksById[property.id]} />
                          </td>
                        )}
                        <td>{formatDate(property.created_at)}</td>
                        <td>
                          {canManage ? (
                            <div className="stats-table__actions">
                              <button
                                className="stats-table__edit"
                                title="Edit property"
                                onClick={() => setEditingProperty(property)}
                              >
                                <Icon name="edit" size={16} />
                              </button>
                              <button
                                className="stats-table__delete"
                                title="Delete property"
                                onClick={() => setDeletingProperty(property)}
                              >
                                <Icon name="trash" size={16} />
                              </button>
                            </div>
                          ) : (
                            <span className="stats-table__readonly">View only</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              {/* Mobile Cards */}
              <div className="stats-cards">
                {properties.map((property) => {
                  const thumb = getPropertyThumb(property);
                  const canManage = isAdmin || listingScope === 'mine';
                  return (
                    <div key={property.id} className="stats-card-item">
                      <div className="stats-card-item__top">
                        {thumb ? (
                          <img
                            src={thumb}
                            alt={property.title}
                            className="stats-card-item__thumb"
                            loading="lazy"
                          />
                        ) : (
                          <div className="stats-card-item__thumb-placeholder">
                            <Icon name="mapPin" size={20} />
                          </div>
                        )}
                        <div className="stats-card-item__info">
                          <p className="stats-card-item__title">
                            <span className="stats-card-item__title-text">{property.title}</span>
                            {confirmedIds.has(property.id) && <ConfirmedShield size={14} />}
                          </p>
                          <div className="stats-card-item__badges">
                            <ListingTypeBadge type={property.listing_type} />
                            <span className={`stats-status stats-status--${property.is_active !== false ? 'active' : 'inactive'}`}>
                              {property.is_active !== false ? 'Active' : 'Inactive'}
                            </span>
                          </div>
                        </div>
                        {canManage && (
                          <div className="stats-card-item__actions">
                            <button
                              className="stats-table__edit"
                              title="Edit property"
                              onClick={() => setEditingProperty(property)}
                            >
                              <Icon name="edit" size={16} />
                            </button>
                            <button
                              className="stats-table__delete"
                              title="Delete property"
                              onClick={() => setDeletingProperty(property)}
                            >
                              <Icon name="trash" size={16} />
                            </button>
                          </div>
                        )}
                      </div>
                      <div className="stats-card-item__meta">
                        <span className="stats-card-item__stat">
                          {formatPrice(property.price, property.currency, property.listing_type)}
                        </span>
                        <span className="stats-card-item__stat">
                          <Icon name="barChart" size={12} />
                          <strong>{(property.view_count ?? 0).toLocaleString()}</strong> views
                        </span>
                        <span className="stats-card-item__stat">
                          {formatDate(property.created_at)}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="stats-pagination">
                  <button
                    className="stats-pagination__btn"
                    disabled={currentPage === 0}
                    onClick={() => setCurrentPage((p) => Math.max(0, p - 1))}
                  >
                    <Icon name="chevronLeft" size={14} />
                    Previous
                  </button>
                  <span className="stats-pagination__info">
                    Page {currentPage + 1} of {totalPages}
                  </span>
                  <button
                    className="stats-pagination__btn"
                    disabled={currentPage >= totalPages - 1}
                    onClick={() => setCurrentPage((p) => p + 1)}
                  >
                    Next
                    <Icon name="chevronRight" size={14} />
                  </button>
                </div>
              )}
            </>
          )}
        </section>
      </div>

      <AddPropertyModal
        isOpen={showAddModal}
        onClose={() => setShowAddModal(false)}
        token={token}
      />

      {editingProperty && (
        <EditPropertyModal
          isOpen={!!editingProperty}
          onClose={() => setEditingProperty(null)}
          token={token}
          property={editingProperty}
        />
      )}

      <ConfirmDeleteDialog
        isOpen={!!deletingProperty}
        onClose={() => setDeletingProperty(null)}
        onConfirm={handleDeleteConfirm}
        title="Delete Property"
        message={`Are you sure you want to delete "${deletingProperty?.title}"? This will deactivate the listing.`}
        isLoading={deleting}
      />

      <UnifiedSearchPanel
        isOpen={showSearch}
        onClose={() => setShowSearch(false)}
        token={token}
        userRole={user.role}
        currentUserId={user.id}
      />
    </div>
  );
};

export default StatsPage;
