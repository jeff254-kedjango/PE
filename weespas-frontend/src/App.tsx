// src/App.tsx
import React, { useState, useEffect, useMemo, lazy, Suspense } from 'react';
import { reportSessionGeo } from './api/analytics';
import { BrowserRouter as Router, Routes, Route, Navigate, useMatch, useNavigate } from 'react-router-dom';
import Navbar from './components/layout/Navbar';
import Hero from './components/layout/Hero';
import SearchPanel from './components/layout/SearchPanel';
import PropertyGallery from './components/layout/PropertyGallery';
import Footer from './components/layout/Footer';
import MobileBottomNav from './components/layout/MobileBottomNav';
import ScrollToTop from './components/layout/ScrollToTop';
import PropertyList from './components/listings/PropertyList';
import PropertyMap from './components/map/PropertyMap';
import SortControls from './components/ui/SortControls';
import ViewToggle, { ViewMode } from './components/ui/ViewToggle';
import SaveSearchButton from './components/ui/SaveSearchButton';
import ContentModeToggle, { ContentMode } from './components/ui/ContentModeToggle';
import ShortsShelf from './components/shorts/ShortsShelf';
import VerticalVideoFeed from './components/shorts/VerticalVideoFeed';
import AdvancedSearchModal from './components/ui/AdvancedSearchModal';
import PropertyDetails from './components/property/PropertyDetails';
import RouteErrorBoundary from './components/ui/RouteErrorBoundary';
import PageTransition from './components/ui/PageTransition';
import PageMeta from './components/ui/PageMeta';

// Lazy-loaded pages (route-level code splitting)
const FavoritesPage = lazy(() => import('./pages/FavoritesPage'));
const LoginPage = lazy(() => import('./pages/LoginPage'));
const RegisterPage = lazy(() => import('./pages/RegisterPage'));
const ProfilePage = lazy(() => import('./pages/ProfilePage'));
const StatsPage = lazy(() => import('./pages/StatsPage'));
const AdminPage = lazy(() => import('./pages/AdminPage'));
const StaffPage = lazy(() => import('./pages/StaffPage'));
const CustomerCarePage = lazy(() => import('./pages/CustomerCarePage'));
const AgentsPage = lazy(() => import('./pages/AgentsPage'));
const AgentProfilePage = lazy(() => import('./pages/AgentProfilePage'));
const TradePage = lazy(() => import('./pages/TradePage'));
const SellerConsolePage = lazy(() => import('./pages/SellerConsolePage'));
// §8 storefront page — /shop/:key handles both the canonical shareable URL (/shop/@<handle>) and
// the legacy fallback (/shop/<sellerId>). The "@" prefix inside the SAME param slot disambiguates
// the two families — no route ordering ambiguity, no shadow matches.
const ShopPage = lazy(() => import('./pages/ShopPage'));
import { AuthProvider, useAuth } from './context/AuthContext';
import { ToastProvider, useToast } from './context/ToastContext';
import { RevealProvider, useReveal } from './context/RevealContext';
import ToastContainer from './components/ui/Toast';
import SoftGate from './components/billing/SoftGate';
import { usePropertySearch } from './hooks/usePropertySearch';
import { useNearbySearch } from './hooks/useNearbySearch';
import { useShortsFeed } from './hooks/useShortsFeed';
import { usePropertyDetails } from './hooks/usePropertyDetails';
import { useFavorites } from './hooks/useFavorites';
import { useDismissals } from './hooks/useDismissals';
import { useFilterParams } from './hooks/useFilterParams';
import { Property, PropertyFilterParams } from './types/propertyApi';
import './App.css';

const RouteLoader = () => (
  <div className="route-loading">
    <div className="route-loading__spinner" />
  </div>
);

const AppContent: React.FC = () => {
  const { toast } = useToast();
  const { token } = useAuth();
  const { requestReveal } = useReveal();
  const { favoriteCount } = useFavorites();
  const { isDismissed, dismiss } = useDismissals();
  const { filters, setFilters, searchApplied, setSearchApplied, handleFilterChange } = useFilterParams();
  const [selectedPropertyId, setSelectedPropertyId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [contentMode, setContentMode] = useState<ContentMode>('image');
  // When a user clicks a short in the horizontal shelf, we open the vertical
  // feed and start playback at that exact short. Null means "open at the top".
  const [videoFeedInitialId, setVideoFeedInitialId] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  // Report browser geolocation once per session for analytics (silent — only if already granted).
  useEffect(() => {
    if (sessionStorage.getItem('weespas_geo_reported')) return;
    if (!('permissions' in navigator) || !navigator.geolocation) return;
    navigator.permissions
      .query({ name: 'geolocation' as PermissionName })
      .then((status) => {
        if (status.state !== 'granted') return;
        navigator.geolocation.getCurrentPosition(
          (position) => {
            reportSessionGeo(position.coords.latitude, position.coords.longitude)
              .then(() => sessionStorage.setItem('weespas_geo_reported', '1'))
              .catch(() => { /* best-effort */ });
          },
          () => { /* user revoked or timeout — IP fallback handles it */ },
          { timeout: 5000, maximumAge: 1000 * 60 * 60 },
        );
      })
      .catch(() => { /* permissions API unavailable */ });
  }, []);

  // Deep-link support: a verification notification (and any shared/bookmarked link) points at
  // `/properties/:id` (optionally `?confirm=1`). The property view is a panel over the home
  // page driven by `selectedPropertyId` — NOT a routed page — so we mirror the URL :id into
  // that same state. One source of truth, one code path: a deep-link then behaves exactly like
  // a card click (full-record resolve, ?confirm=1 auto-open all happen downstream unchanged).
  const navigate = useNavigate();
  const deepLinkMatch = useMatch('/properties/:id'); // query string is ignored by route matching
  const deepLinkId = deepLinkMatch?.params.id ?? null;
  useEffect(() => {
    if (deepLinkId) setSelectedPropertyId(deepLinkId);
  }, [deepLinkId]);

  // Closing the panel clears the selection; if we arrived via a deep-link URL, return to `/`
  // so the address bar matches what's on screen. A card-click open (URL still `/`,
  // deepLinkMatch null) closes exactly as before — no navigation.
  const closeDetails = () => {
    setSelectedPropertyId(null);
    if (deepLinkMatch) navigate('/');
  };

  const {
    pages: allPages,
    properties: allProperties,
    isLoading: allLoading,
    isError: allError,
    error: allErrorMessage,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage
  } = usePropertySearch(
    searchApplied ? filters : { skip: 0, limit: 12, sort_by: filters.sort_by, sort_order: filters.sort_order },
    token,
  );

  const SHELF_PAGE_SIZE = 6; // shorts per inline shelf
  const {
    items: shortsItems,
    fetchNextPage: fetchNextShortsPage,
    hasNextPage: hasNextShortsPage,
    isLoading: shortsLoading,
    isError: shortsError,
  } = useShortsFeed(token, SHELF_PAGE_SIZE);

  const {
    properties: nearbyProperties,
    isLoading: nearbyLoading,
    isError: nearbyError,
    error: nearbyErrorMessage,
    setQuery: setNearbyQuery
  } = useNearbySearch(filters);

  const selectedPropertyQuery = usePropertyDetails(selectedPropertyId);
  const hasGeo = filters.latitude !== undefined && filters.longitude !== undefined;
  const useNearby = searchApplied && hasGeo;
  const rawDisplayProperties: Property[] = useNearby ? nearbyProperties : allProperties;
  const displayProperties: Property[] = rawDisplayProperties.filter((p) => !isDismissed(p.id));
  const listLoading = useNearby ? nearbyLoading : allLoading;
  const listError = useNearby ? nearbyErrorMessage : allErrorMessage;

  // Default home feed: render per-page, with a Shorts shelf injected before
  // every page after the first. Filtered (nearby) mode keeps the flat list.
  const useInterleavedFeed = !useNearby;
  const filteredPages = useMemo(
    () => (useInterleavedFeed
      ? allPages.map((page) => page.items.filter((p) => !isDismissed(p.id)))
      : []),
    [allPages, isDismissed, useInterleavedFeed],
  );
  const visibleShorts = useMemo(
    () => (isDismissed ? shortsItems.filter((s) => !isDismissed(s.id)) : shortsItems),
    [shortsItems, isDismissed],
  );

  const activeProperty = selectedPropertyQuery.data ?? displayProperties.find((property) => property.id === selectedPropertyId) ?? displayProperties[0] ?? null;

  const handleSearch = () => {
    setSearchApplied(true);
    setNearbyQuery({ ...filters, skip: 0, limit: 20 });
    toast.info('Search results updated');
    setTimeout(() => {
      document.getElementById('listings')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 100);
  };

  const handleUseLocation = () => {
    if (!navigator.geolocation) return;

    navigator.geolocation.getCurrentPosition(
      (position) => {
        const lat = Number(position.coords.latitude.toFixed(4));
        const lng = Number(position.coords.longitude.toFixed(4));
        const updatedFilters = { ...filters, latitude: lat, longitude: lng };
        setFilters(updatedFilters);
        setSearchApplied(true);
        setNearbyQuery({ ...updatedFilters, skip: 0, limit: 20 });
        toast.success('Location access granted');
        reportSessionGeo(position.coords.latitude, position.coords.longitude)
          .then(() => sessionStorage.setItem('weespas_geo_reported', '1'))
          .catch(() => { /* best-effort */ });
      },
      (error) => {
        console.error(error);
        toast.error('Unable to access your location');
      }
    );
  };

  const handleSortChange = (sort_by: PropertyFilterParams['sort_by'], sort_order: PropertyFilterParams['sort_order']) => {
    const updated = { ...filters, sort_by, sort_order };
    setFilters(updated);
    if (searchApplied) {
      setNearbyQuery({ ...updated, skip: 0, limit: 20 });
    }
  };

  const loadMore = () => {
    if (hasNextPage) {
      fetchNextPage();
      // Parallel: pull the corresponding shorts slice so the shelf and the new
      // property cards appear together (no second network round-trip cost).
      if (hasNextShortsPage) fetchNextShortsPage();
    }
  };

  const handleDismiss = (property: Property) => {
    dismiss(property.id);
    toast.success("Listing hidden — you won't see this again");
  };

  // The home view. Rendered by BOTH `/` and the `/properties/:id` deep-link route so the
  // property details panel (driven by `selectedPropertyId`) appears OVER the normal home page,
  // matching how a card click opens it — no separate routed page to keep in sync.
  const homeElement = (
            <RouteErrorBoundary>
            <>
              <PageMeta title="Home" description="Find verified spaces, trade with local shops, and move seamlessly across Kenya with Weespas — your spatial platform for listings, commerce, and mobility." />
              <Hero />
              <section className="landing-grid">
                <PropertyGallery
                  selectedPropertyId={activeProperty?.id ?? null}
                  onSelect={(property) => setSelectedPropertyId(property.id)}
                  userLocation={
                    filters.latitude !== undefined && filters.longitude !== undefined
                      ? { latitude: filters.latitude, longitude: filters.longitude }
                      : undefined
                  }
                />
              </section>
              <div id="listings" className="preview-section">
                <div className="preview-header">
                  <div>
                    <p className="eyebrow">Marketplace Preview</p>
                    <h2>Latest properties near you</h2>
                  </div>
                  {/* Filter relocated here from the landing-grid sidebar: a compact filter-icon
                      trigger that opens a localized popover, replacing the old "N listings
                      available" count (that count already appears in the preview-controls row). */}
                  <SearchPanel
                    filters={filters}
                    onChange={handleFilterChange}
                    onSearch={handleSearch}
                    onUseLocation={handleUseLocation}
                    onAdvancedSearch={() => setAdvancedOpen(true)}
                  />
                </div>
                <div className="preview-controls">
                  <SortControls
                    sortBy={filters.sort_by ?? 'created_at'}
                    sortOrder={filters.sort_order ?? 'desc'}
                    onChange={handleSortChange}
                    resultCount={displayProperties.length}
                  />
                  <ContentModeToggle
                    mode={contentMode}
                    onChange={(next) => { setVideoFeedInitialId(null); setContentMode(next); }}
                  />
                  <ViewToggle mode={viewMode} onChange={setViewMode} />
                  <SaveSearchButton filters={filters as unknown as Record<string, unknown>} searchApplied={searchApplied} />
                </div>
                {viewMode === 'list' ? (
                  <>
                    {useInterleavedFeed ? (
                      <div className="interleaved-feed">
                        {filteredPages.map((pageItems, pageIdx) => {
                          if (pageItems.length === 0) return null;
                          const shelfStart = (pageIdx - 1) * SHELF_PAGE_SIZE;
                          const shelfShorts = pageIdx > 0
                            ? visibleShorts.slice(shelfStart, shelfStart + SHELF_PAGE_SIZE)
                            : [];
                          return (
                            <React.Fragment key={`page-${pageIdx}`}>
                              {pageIdx > 0 && (
                                <ShortsShelf
                                  shorts={shelfShorts}
                                  isLoading={shortsLoading && shelfShorts.length === 0}
                                  isError={shortsError}
                                  onSelect={(id) => { setVideoFeedInitialId(id); setContentMode('video'); }}
                                  onDismiss={(id) => { dismiss(id); toast.success("Listing hidden — you won't see this again"); }}
                                  onSeeAll={() => { setVideoFeedInitialId(null); setContentMode('video'); }}
                                  heading={pageIdx === 1 ? 'Watch homes near you' : 'More homes on video'}
                                />
                              )}
                              <PropertyList
                                properties={pageItems}
                                onSelect={(property: Property) => setSelectedPropertyId(property.id)}
                                onDismiss={handleDismiss}
                                loading={false}
                                error={null}
                              />
                            </React.Fragment>
                          );
                        })}
                        {/* First-load / error states — only when there are no pages yet. */}
                        {filteredPages.length === 0 && (
                          <PropertyList
                            properties={[]}
                            onSelect={(property: Property) => setSelectedPropertyId(property.id)}
                            onDismiss={handleDismiss}
                            loading={listLoading}
                            error={listError ? String(listError) : null}
                          />
                        )}
                      </div>
                    ) : (
                      <PropertyList
                        properties={displayProperties}
                        onSelect={(property: Property) => setSelectedPropertyId(property.id)}
                        onDismiss={handleDismiss}
                        loading={listLoading}
                        error={listError ? String(listError) : null}
                      />
                    )}
                    {hasNextPage && (
                      <div className="load-more-wrap">
                        <button type="button" className="secondary-button" onClick={loadMore} disabled={isFetchingNextPage}>
                          {isFetchingNextPage ? 'Loading more...' : 'Load more'}
                        </button>
                      </div>
                    )}
                  </>
                ) : (
                  <PropertyMap
                    properties={displayProperties}
                    onSelect={(property: Property) => setSelectedPropertyId(property.id)}
                    loading={listLoading}
                    center={filters.latitude && filters.longitude ? [filters.latitude, filters.longitude] : undefined}
                    onGetDirections={async (property: Property) => {
                      const coords = await requestReveal(property.id);
                      if (coords) window.open(coords.directions_url, '_blank', 'noopener,noreferrer');
                    }}
                  />
                )}
              </div>
            </>
            </RouteErrorBoundary>
  );

  return (
    <div className="app">
      <Navbar />
      <PageTransition>
      <Routes>
        <Route path="/" element={homeElement} />
        {/* Deep-link (verification notification / shared link): render home, then the
            URL→state effect opens the details panel for :id over it. */}
        <Route path="/properties/:id" element={homeElement} />
        <Route path="/favorites" element={<RouteErrorBoundary><Suspense fallback={<RouteLoader />}><FavoritesPage /></Suspense></RouteErrorBoundary>} />
        <Route path="/login" element={<RouteErrorBoundary><Suspense fallback={<RouteLoader />}><LoginPage /></Suspense></RouteErrorBoundary>} />
        <Route path="/register" element={<RouteErrorBoundary><Suspense fallback={<RouteLoader />}><RegisterPage /></Suspense></RouteErrorBoundary>} />
        <Route path="/profile" element={<RouteErrorBoundary><Suspense fallback={<RouteLoader />}><ProfilePage /></Suspense></RouteErrorBoundary>} />
        <Route path="/stats" element={<RouteErrorBoundary><Suspense fallback={<RouteLoader />}><StatsPage /></Suspense></RouteErrorBoundary>} />
        <Route path="/admin" element={<RouteErrorBoundary><Suspense fallback={<RouteLoader />}><AdminPage /></Suspense></RouteErrorBoundary>} />
        <Route path="/staff" element={<RouteErrorBoundary><Suspense fallback={<RouteLoader />}><StaffPage /></Suspense></RouteErrorBoundary>} />
        <Route path="/customer-care" element={<RouteErrorBoundary><Suspense fallback={<RouteLoader />}><CustomerCarePage /></Suspense></RouteErrorBoundary>} />
        <Route path="/agents" element={<RouteErrorBoundary><Suspense fallback={<RouteLoader />}><AgentsPage onOpenProperty={setSelectedPropertyId} /></Suspense></RouteErrorBoundary>} />
        <Route path="/agents/:agentId" element={<RouteErrorBoundary><Suspense fallback={<RouteLoader />}><AgentProfilePage /></Suspense></RouteErrorBoundary>} />
        {/* Commerce buyer feed (FE-1). The :sellerId variant deep-links a storefront panel open. */}
        <Route path="/trade" element={<RouteErrorBoundary><Suspense fallback={<RouteLoader />}><TradePage /></Suspense></RouteErrorBoundary>} />
        <Route path="/trade/sell" element={<RouteErrorBoundary><Suspense fallback={<RouteLoader />}><SellerConsolePage /></Suspense></RouteErrorBoundary>} />
        <Route path="/trade/sellers/:sellerId" element={<RouteErrorBoundary><Suspense fallback={<RouteLoader />}><TradePage /></Suspense></RouteErrorBoundary>} />
        {/* §8 shop URL — ONE route, one component. The :key param carries either "@<handle>"
            (canonical shareable URL) or a bare sellerId (legacy fallback). ShopPage inspects the
            "@" prefix to pick the entry, and if a sellerId resolves to a shop with a handle it
            Navigates replace to /shop/@<handle> (frontend-only canonical redirect). */}
        <Route path="/shop/:key" element={<RouteErrorBoundary><Suspense fallback={<RouteLoader />}><ShopPage /></Suspense></RouteErrorBoundary>} />
        {/* Unknown URL (typo, stale/deleted link) → home, never a blank page. */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      </PageTransition>
      {contentMode === 'video' && (
        <VerticalVideoFeed
          token={token}
          onSelect={(id) => setSelectedPropertyId(id)}
          onDismiss={(id) => { dismiss(id); toast.success("Listing hidden — you won't see this again"); }}
          isDismissed={isDismissed}
          onExit={() => { setContentMode('image'); setVideoFeedInitialId(null); }}
          initialShortId={videoFeedInitialId}
        />
      )}
      {/* PropertyDetails is rendered AFTER VerticalVideoFeed so that — when both are
          mounted (user clicked Details from inside the video feed) — the details panel
          stacks above the feed. Both use --z-modal, so DOM order is the tiebreaker. */}
      {selectedPropertyId && activeProperty?.id === selectedPropertyId && (
        <PropertyDetails
          property={activeProperty}
          onClose={closeDetails}
        />
      )}
      <AdvancedSearchModal
        isOpen={advancedOpen}
        onClose={() => setAdvancedOpen(false)}
        filters={filters}
        onApply={(advFilters) => {
          handleFilterChange(advFilters);
          handleSearch();
        }}
      />
      <Footer />
      <MobileBottomNav
        favoriteCount={favoriteCount}
        viewMode={viewMode}
        onMapToggle={() => {
          setViewMode((prev) => (prev === 'map' ? 'list' : 'map'));
          setTimeout(() => {
            document.getElementById('listings')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
          }, 100);
        }}
      />
    </div>
  );
};

const App: React.FC = () => {
  return (
    <Router>
      <ScrollToTop />
      <AuthProvider>
        <ToastProvider>
          <RevealProvider>
            <AppContent />
            <ToastContainer />
            <SoftGate />
          </RevealProvider>
        </ToastProvider>
      </AuthProvider>
    </Router>
  );
};

export default App;
