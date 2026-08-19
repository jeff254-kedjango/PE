// src/components/listings/PropertyList.tsx
import React from 'react';
import PropertyCard from './PropertyCard';
import { Property } from '../../types/propertyApi';
import './PropertyList.css';

interface PropertyListProps {
  properties: Property[];
  onSelect: (property: Property) => void;
  loading: boolean;
  error: string | null;
  onDismiss?: (property: Property) => void;
}

const PropertyList: React.FC<PropertyListProps> = ({ properties, onSelect, loading, error, onDismiss }) => {
  if (loading) {
    return (
      <div className="property-grid property-grid--loading">
        {Array.from({ length: 6 }).map((_, index) => (
          <div key={index} className="property-card property-card--skeleton">
            <div className="skeleton-block skeleton-image" />
            <div className="skeleton-block skeleton-line" />
            <div className="skeleton-block skeleton-line short" />
          </div>
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="empty-state">
        <p className="empty-state__title">Unable to load listings</p>
        <p className="empty-state__copy">{error}</p>
      </div>
    );
  }

  if (!properties.length) {
    return (
      <div className="empty-state">
        <p className="empty-state__title">No results found</p>
        <p className="empty-state__copy">Try a broader radius, different category, or use your location.</p>
      </div>
    );
  }

  return (
    <div className="property-grid">
      {properties.map((prop) => (
        <PropertyCard key={prop.id} property={prop} onSelect={onSelect} onDismiss={onDismiss} />
      ))}
    </div>
  );
};

export default PropertyList;