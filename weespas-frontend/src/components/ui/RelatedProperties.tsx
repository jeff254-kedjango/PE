import React, { useCallback, useEffect, useRef, useState } from 'react';
import Icon from './Icon';
import ListingTypeBadge from './ListingTypeBadge';
import VerifiedBadge from './VerifiedBadge';
import { formatPrice } from '../../utils/format';
import { resolveMediaUrl } from '../../utils/media';
import type { Property } from '../../types/propertyApi';
import './RelatedProperties.css';

const CARD_WIDTH = 220;
const CARD_GAP = 16;
const SCROLL_STEP = CARD_WIDTH + CARD_GAP;
const AUTO_SCROLL_MS = 4000;
const RESUME_DELAY_MS = 6000;

/* ── Compact card for the carousel ── */
const RelatedPropertyCard = React.memo<{
  property: Property;
  onSelect: (p: Property) => void;
}>(({ property, onSelect }) => {
  const image = property.main_image
    ?? property.images?.find((img) => img.is_main)
    ?? property.images?.[0];
  const imgSrc = resolveMediaUrl(image?.thumbnail_url || image?.url);

  return (
    <button type="button" className="rp-card" onClick={() => onSelect(property)}>
      <div className="rp-card__image">
        {imgSrc ? (
          <img src={imgSrc} alt={image?.alt_text ?? property.title} loading="lazy" />
        ) : (
          <div className="rp-card__image-fallback">
            <Icon name="home" size={24} />
          </div>
        )}
        {property.listing_type && (
          <div className="rp-card__badge">
            <ListingTypeBadge type={property.listing_type} />
          </div>
        )}
      </div>
      <div className="rp-card__body">
        <h3 className="rp-card__title">{property.title}</h3>
        {property.location_name && (
          <p className="rp-card__location">
            <Icon name="mapPin" size={11} />
            {property.location_name}
          </p>
        )}
        <div className="rp-card__specs">
          {property.bedrooms != null && property.bedrooms > 0 && (
            <span className="rp-card__spec">
              <Icon name="bed" size={12} /> {property.bedrooms} {property.bedrooms === 1 ? 'Bed' : 'Beds'}
            </span>
          )}
          {property.bathrooms != null && property.bathrooms > 0 && (
            <span className="rp-card__spec">
              <Icon name="bath" size={12} /> {property.bathrooms} {property.bathrooms === 1 ? 'Bath' : 'Baths'}
            </span>
          )}
        </div>
        <div className="rp-card__footer">
          {property.is_engineer_certified && <VerifiedBadge size={14} />}
          <strong className="rp-card__price">
            {formatPrice(property.price, property.currency, property.listing_type)}
          </strong>
        </div>
      </div>
    </button>
  );
});
RelatedPropertyCard.displayName = 'RelatedPropertyCard';

/* ── Skeleton card ── */
const SkeletonCard = () => (
  <div className="rp-skeleton" aria-hidden="true">
    <div className="rp-skeleton__image" />
    <div className="rp-skeleton__body">
      <div className="rp-skeleton__line" style={{ width: '75%', height: 13 }} />
      <div className="rp-skeleton__line" style={{ width: '55%', height: 11 }} />
      <div className="rp-skeleton__line" style={{ width: '40%', height: 11 }} />
    </div>
  </div>
);

/* ── Main component ── */
interface RelatedPropertiesProps {
  properties: Property[];
  isLoading: boolean;
  onSelect: (property: Property) => void;
  title?: string;
}

const RelatedProperties: React.FC<RelatedPropertiesProps> = ({
  properties,
  isLoading,
  onSelect,
  title = 'Related Properties',
}) => {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [paused, setPaused] = useState(false);
  const resumeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  /* ── Auto-scroll ── */
  useEffect(() => {
    if (paused || !properties.length) return;

    const id = setInterval(() => {
      const el = scrollRef.current;
      if (!el) return;

      const atEnd = el.scrollLeft + el.clientWidth >= el.scrollWidth - 4;
      if (atEnd) {
        el.scrollTo({ left: 0, behavior: 'smooth' });
      } else {
        el.scrollBy({ left: SCROLL_STEP, behavior: 'smooth' });
      }
    }, AUTO_SCROLL_MS);

    return () => clearInterval(id);
  }, [paused, properties.length]);

  /* ── Pause / resume helpers ── */
  const pause = useCallback(() => {
    setPaused(true);
    if (resumeTimer.current) clearTimeout(resumeTimer.current);
  }, []);

  const scheduleResume = useCallback(() => {
    if (resumeTimer.current) clearTimeout(resumeTimer.current);
    resumeTimer.current = setTimeout(() => setPaused(false), RESUME_DELAY_MS);
  }, []);

  const handleMouseEnter = useCallback(() => setPaused(true), []);
  const handleMouseLeave = useCallback(() => setPaused(false), []);

  /* ── Arrow handlers ── */
  const scrollLeft = useCallback(() => {
    scrollRef.current?.scrollBy({ left: -SCROLL_STEP, behavior: 'smooth' });
    pause();
    scheduleResume();
  }, [pause, scheduleResume]);

  const scrollRight = useCallback(() => {
    scrollRef.current?.scrollBy({ left: SCROLL_STEP, behavior: 'smooth' });
    pause();
    scheduleResume();
  }, [pause, scheduleResume]);

  /* Cleanup resume timer */
  useEffect(() => {
    return () => { if (resumeTimer.current) clearTimeout(resumeTimer.current); };
  }, []);

  /* Don't render if nothing to show and not loading */
  if (!isLoading && properties.length === 0) return null;

  return (
    <section className="rp-section">
      <div className="rp-header">
        <h2 className="rp-title">
          <Icon name="home" size={20} />
          {title}
        </h2>
        {properties.length > 0 && (
          <span className="rp-count">
            {properties.length} {properties.length === 1 ? 'property' : 'properties'}
          </span>
        )}
      </div>

      <div
        className="rp-carousel"
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
      >
        {/* Left arrow */}
        <button
          type="button"
          className="rp-arrow rp-arrow--left"
          onClick={scrollLeft}
          aria-label="Scroll left"
        >
          <Icon name="chevronLeft" size={18} />
        </button>

        {/* Scroll container */}
        <div className="rp-scroll" ref={scrollRef} onTouchStart={pause}>
          <div className="rp-track">
            {isLoading
              ? Array.from({ length: 5 }).map((_, i) => <SkeletonCard key={i} />)
              : properties.map((property) => (
                  <RelatedPropertyCard
                    key={property.id}
                    property={property}
                    onSelect={onSelect}
                  />
                ))
            }
          </div>
        </div>

        {/* Right arrow */}
        <button
          type="button"
          className="rp-arrow rp-arrow--right"
          onClick={scrollRight}
          aria-label="Scroll right"
        >
          <Icon name="chevronRight" size={18} />
        </button>
      </div>
    </section>
  );
};

export default RelatedProperties;
