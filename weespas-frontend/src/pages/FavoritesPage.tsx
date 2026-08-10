import React, { useState } from 'react';
import { createPortal } from 'react-dom';
import { useQueries } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useFavorites } from '../hooks/useFavorites';
import { useRelatedProperties } from '../hooks/useRelatedProperties';
import { fetchPropertyDetails } from '../api/properties';
import PropertyList from '../components/listings/PropertyList';
import PropertyMap from '../components/map/PropertyMap';
import PropertyDetailsById from '../components/property/PropertyDetailsById';
import RelatedProperties from '../components/ui/RelatedProperties';
import ViewToggle, { ViewMode } from '../components/ui/ViewToggle';
import Icon from '../components/ui/Icon';
import { Property } from '../types/propertyApi';
import PageMeta from '../components/ui/PageMeta';
import './FavoritesPage.css';

const FavoritesPage: React.FC = () => {
  const { favorites, favoriteCount } = useFavorites();
  // Track the clicked id + the shallow card (so PropertyDetails opens
  // instantly with main_image, then upgrades to the full gallery once
  // /properties/:id resolves — no perceptible latency, no spinner gate).
  const [selectedProperty, setSelectedProperty] = useState<Property | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>('list');

  const propertyQueries = useQueries({
    queries: favorites.map((id) => ({
      queryKey: ['property', id],
      queryFn: () => fetchPropertyDetails(id),
      staleTime: 1000 * 60 * 2,
      gcTime: 1000 * 60 * 15,
      retry: 1,
    })),
  });

  const isLoading = propertyQueries.some((q) => q.isLoading);
  const properties = propertyQueries
    .filter((q) => q.isSuccess && q.data)
    .map((q) => q.data as Property);

  const { properties: relatedProperties, isLoading: relatedLoading } = useRelatedProperties(properties);

  if (favoriteCount === 0) {
    return (
      <div className="favorites-page">
        <PageMeta title="Saved Properties" description="View and manage your saved property listings on Weespas." />
        <div className="favorites-header">
          <p className="eyebrow">Your Collection</p>
          <h1>Saved Properties</h1>
        </div>
        <div className="favorites-empty">
          <span className="favorites-empty__icon">
            <Icon name="heart" size={48} />
          </span>
          <p className="empty-state__title">No saved properties yet</p>
          <p className="empty-state__copy">
            Browse listings and tap the heart icon to save properties you love.
          </p>
          <Link to="/" className="favorites-empty__cta">
            Explore Properties
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="favorites-page">
      <PageMeta title="Saved Properties" description="View and manage your saved property listings on Weespas." />
      <div className="favorites-header">
        <div>
          <p className="eyebrow">Your Collection</p>
          <h1>Saved Properties</h1>
        </div>
        <div className="favorites-controls">
          <span className="preview-meta">{favoriteCount} saved</span>
          <ViewToggle mode={viewMode} onChange={setViewMode} />
        </div>
      </div>
      {viewMode === 'list' ? (
        <PropertyList
          properties={properties}
          onSelect={(property) => setSelectedProperty(property)}
          loading={isLoading}
          error={null}
        />
      ) : (
        <PropertyMap
          properties={properties}
          onSelect={(property) => setSelectedProperty(property)}
          loading={isLoading}
        />
      )}
      <RelatedProperties
        properties={relatedProperties}
        isLoading={relatedLoading}
        onSelect={(property) => setSelectedProperty(property)}
        title="Related Properties"
      />
      {selectedProperty && createPortal(
        <PropertyDetailsById
          propertyId={selectedProperty.id}
          fallbackProperty={selectedProperty}
          onClose={() => setSelectedProperty(null)}
        />,
        document.body
      )}
    </div>
  );
};

export default FavoritesPage;
