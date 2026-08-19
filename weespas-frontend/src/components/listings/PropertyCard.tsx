// src/components/listings/PropertyCard.tsx
import React from 'react';
import { Property } from '../../types/propertyApi';
import { formatPrice } from '../../utils/format';
import { resolveMediaUrl } from '../../utils/media';
import VerifiedBadge from '../ui/VerifiedBadge';
import ListingTypeBadge from '../ui/ListingTypeBadge';
import DismissButton from '../ui/DismissButton';
import './PropertyCard.css';

interface PropertyCardProps {
  property: Property;
  onSelect: (property: Property) => void;
  onDismiss?: (property: Property) => void;
}

const formatLocation = (property: Property): string | null => {
  const addr = property.address;
  const parts = [
    addr?.location_name ?? property.location_name,
    addr?.city,
    addr?.county,
  ].filter((part): part is string => Boolean(part && part.trim()));
  if (parts.length === 0) return null;
  // Deduplicate consecutive duplicates (e.g., location_name === city)
  const deduped = parts.filter((p, i) => p.toLowerCase() !== parts[i - 1]?.toLowerCase());
  return deduped.slice(0, 2).join(', ');
};

const PropertyCard: React.FC<PropertyCardProps> = ({ property, onSelect, onDismiss }) => {
  // Resolve image: main_image (from list endpoint) → images[0] (from detail endpoint) → null
  const image = property.main_image
    ?? property.images?.find((img) => img.is_main)
    ?? property.images?.[0];
  const imgSrc = resolveMediaUrl(image?.thumbnail_url || image?.url);
  const locationLabel = formatLocation(property);
  const hasDistance = property.distance !== undefined && property.distance !== null;

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onSelect(property);
    }
  };

  return (
    <div
      className="property-card"
      role="button"
      tabIndex={0}
      onClick={() => onSelect(property)}
      onKeyDown={handleKeyDown}
    >
      {onDismiss && (
        <DismissButton onDismiss={() => onDismiss(property)} />
      )}
      <div className="property-card__image">
        {imgSrc ? (
          <img
            src={imgSrc}
            alt={image?.alt_text ?? property.title}
            loading="lazy"
            width={160}
            height={120}
          />
        ) : (
          <span>{property.title?.slice(0, 1)}</span>
        )}
        {property.listing_type && (
          <ListingTypeBadge type={property.listing_type} className="property-card__listing-badge" />
        )}
      </div>
      <div className="property-card__body">
        <div className="property-card__row">
          <h3>{property.title}</h3>
          {hasDistance && (
            <span className="tag">{property.distance!.toFixed(1)} km</span>
          )}
        </div>
        {(locationLabel || (property.bedrooms != null && property.bedrooms > 0)) && (
          <div className="property-card__meta">
            {locationLabel && (
              <span className="property-card__location" title={locationLabel}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
                  <circle cx="12" cy="10" r="3" />
                </svg>
                <span>{locationLabel}</span>
              </span>
            )}
            {property.bedrooms != null && property.bedrooms > 0 && (
              <span className="spec-item">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M3 7v11a2 2 0 002 2h14a2 2 0 002-2V7" />
                  <path d="M21 11H3V7a2 2 0 012-2h14a2 2 0 012 2v4z" />
                  <path d="M7 11V7" />
                </svg>
                {property.bedrooms} {property.bedrooms === 1 ? 'Bed' : 'Beds'}
              </span>
            )}
          </div>
        )}
        <p>{property.description}</p>
      </div>
      <div className="property-card__footer">
        {property.is_engineer_certified && (
          <VerifiedBadge size={18} />
        )}
        {property.price && <strong>{formatPrice(property.price, property.currency, property.listing_type)}</strong>}
      </div>
    </div>
  );
};

export default PropertyCard;