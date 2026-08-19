import React, { useState, useCallback, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useParams, useNavigate } from 'react-router-dom';
import Icon from '../components/ui/Icon';
import VerifiedBadge from '../components/ui/VerifiedBadge';
import ListingTypeBadge from '../components/ui/ListingTypeBadge';
import Pagination from '../components/ui/Pagination';
import PageMeta from '../components/ui/PageMeta';
import PropertyDetailsById from '../components/property/PropertyDetailsById';
import RelatedProperties from '../components/ui/RelatedProperties';
import { useAgentProfile } from '../hooks/useAgentProfile';
import { useAgentPublicProperties } from '../hooks/useAgentPublicProperties';
import { useRelatedProperties } from '../hooks/useRelatedProperties';
import { formatPrice } from '../utils/format';
import { resolveMediaUrl } from '../utils/media';
import { linkify } from '../utils/linkify';
import type { Property, PropertyVideo } from '../types/propertyApi';
import './AgentProfilePage.css';

const PAGE_SIZE = 12;

function whatsappUrl(phone: string, text: string) {
  return `https://wa.me/${phone.replace(/\D/g, '')}?text=${encodeURIComponent(text)}`;
}

function formatDuration(seconds?: number): string {
  if (!seconds) return '';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

/* ── Property Card (Agent Page variant) ── */
const AgentPropertyCard = React.memo<{
  property: Property;
  onSelect: (p: Property) => void;
}>(({ property, onSelect }) => {
  const image = property.main_image
    ?? property.images?.find((img) => img.is_main)
    ?? property.images?.[0];
  const imgSrc = resolveMediaUrl(image?.thumbnail_url || image?.url);

  return (
    <button type="button" className="ap-prop-card" onClick={() => onSelect(property)}>
      <div className="ap-prop-card__image">
        {imgSrc ? (
          <img src={imgSrc} alt={image?.alt_text ?? property.title} loading="lazy" />
        ) : (
          <div className="ap-prop-card__image-fallback">
            <Icon name="home" size={28} />
          </div>
        )}
        {property.listing_type && (
          <div className="ap-prop-card__badge">
            <ListingTypeBadge type={property.listing_type} />
          </div>
        )}
      </div>
      <div className="ap-prop-card__body">
        <h3 className="ap-prop-card__title">{property.title}</h3>
        {property.location_name && (
          <p className="ap-prop-card__location">
            <Icon name="mapPin" size={12} />
            {property.location_name}
          </p>
        )}
        <div className="ap-prop-card__specs">
          {property.bedrooms != null && property.bedrooms > 0 && (
            <span className="ap-prop-card__spec">
              <Icon name="bed" size={13} /> {property.bedrooms} {property.bedrooms === 1 ? 'Bed' : 'Beds'}
            </span>
          )}
          {property.bathrooms != null && property.bathrooms > 0 && (
            <span className="ap-prop-card__spec">
              <Icon name="bath" size={13} /> {property.bathrooms} {property.bathrooms === 1 ? 'Bath' : 'Baths'}
            </span>
          )}
        </div>
        <div className="ap-prop-card__footer">
          {property.is_engineer_certified && <VerifiedBadge size={16} />}
          <strong className="ap-prop-card__price">
            {formatPrice(property.price, property.currency, property.listing_type)}
          </strong>
        </div>
      </div>
    </button>
  );
});
AgentPropertyCard.displayName = 'AgentPropertyCard';

/* ── Short Video Card ── */
const ShortVideoCard = React.memo<{
  video: PropertyVideo & { propertyTitle?: string };
  onPlay: (src: string) => void;
}>(({ video, onPlay }) => {
  const src = resolveMediaUrl(video.streaming_url || video.url)!;
  return (
    <button
      type="button"
      className="ap-short-video"
      onClick={() => onPlay(src)}
    >
      <div className="ap-short-video__thumb">
        {video.thumbnail_url ? (
          <img src={resolveMediaUrl(video.thumbnail_url)} alt={video.title ?? 'Short video'} loading="lazy" />
        ) : (
          <div className="ap-short-video__fallback">
            <Icon name="video" size={32} />
          </div>
        )}
        <div className="ap-short-video__overlay">
          <div className="ap-short-video__play">
            <Icon name="play" size={18} />
          </div>
        </div>
        {video.duration != null && (
          <span className="ap-short-video__duration">{formatDuration(video.duration)}</span>
        )}
      </div>
      <div className="ap-short-video__info">
        {video.title && <p className="ap-short-video__title">{video.title}</p>}
        {video.propertyTitle && (
          <span className="ap-short-video__property">{video.propertyTitle}</span>
        )}
      </div>
    </button>
  );
});
ShortVideoCard.displayName = 'ShortVideoCard';

/* ── Skeleton Components ── */
const SkeletonProfile = () => (
  <div className="ap-profile ap-profile--skeleton" aria-hidden="true">
    <div className="ap-profile__avatar ap-skeleton-pulse" />
    <div className="ap-profile__details">
      <div className="ap-skeleton-line" style={{ width: 180, height: 20, marginBottom: 8 }} />
      <div className="ap-skeleton-line" style={{ width: 120, height: 14, marginBottom: 12 }} />
      <div className="ap-skeleton-line" style={{ width: '100%', height: 14 }} />
      <div className="ap-skeleton-line" style={{ width: '80%', height: 14, marginTop: 4 }} />
    </div>
  </div>
);

const SkeletonPropertyCard = () => (
  <div className="ap-prop-card ap-prop-card--skeleton" aria-hidden="true">
    <div className="ap-prop-card__image ap-skeleton-pulse" />
    <div className="ap-prop-card__body">
      <div className="ap-skeleton-line" style={{ width: '70%', height: 14, marginBottom: 8 }} />
      <div className="ap-skeleton-line" style={{ width: '50%', height: 12, marginBottom: 8 }} />
      <div className="ap-skeleton-line" style={{ width: '40%', height: 12 }} />
    </div>
  </div>
);

/* ── Page Component ── */
const AgentProfilePage: React.FC = () => {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const [currentPage, setCurrentPage] = useState(0);
  // Track the clicked id AND a shallow fallback (so PropertyDetails appears
  // instantly; the gallery fills in when /properties/:id resolves via
  // PropertyDetailsById). Capturing the fallback lets cards from BOTH the
  // agent's own listings AND the "Other Related Properties" carousel open
  // PropertyDetails — the previous derivation only searched `properties`
  // and dropped related clicks on the floor.
  const [selectedProperty, setSelectedProperty] = useState<Property | null>(null);
  const [videoPlayerOpen, setVideoPlayerOpen] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const listingsRef = useRef<HTMLElement>(null);

  const { data: agent, isLoading: agentLoading, isError: agentError } = useAgentProfile(agentId);
  const { data: propertiesData, isLoading: propsLoading } = useAgentPublicProperties(agentId, {
    skip: currentPage * PAGE_SIZE,
    limit: PAGE_SIZE,
  });

  const properties = propertiesData?.items ?? [];
  const totalProperties = propertiesData?.total ?? 0;
  const totalPages = useMemo(() => Math.ceil(totalProperties / PAGE_SIZE), [totalProperties]);

  const { properties: relatedProperties, isLoading: relatedLoading } = useRelatedProperties(properties);

  // Collect all videos from all properties for the short-videos section
  const allVideos = useMemo(() => {
    const vids: (PropertyVideo & { propertyTitle?: string })[] = [];
    for (const prop of properties) {
      if (prop.videos && prop.videos.length > 0) {
        for (const v of prop.videos) {
          vids.push({ ...v, propertyTitle: prop.title });
        }
      }
    }
    return vids;
  }, [properties]);

  const handlePageChange = useCallback((page: number) => {
    setCurrentPage(page);
    listingsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  const handlePropertySelect = useCallback((property: Property) => {
    setSelectedProperty(property);
  }, []);

  const handleGoBack = useCallback(() => {
    navigate(-1);
  }, [navigate]);

  if (agentError) {
    return (
      <div className="ap-page">
        <PageMeta title="Agent Not Found" description="This agent profile could not be found." />
        <div className="ap-error">
          <Icon name="alertTriangle" size={48} />
          <h2>Agent not found</h2>
          <p>This agent profile may have been removed or is no longer available.</p>
          <button type="button" className="ap-error__btn" onClick={handleGoBack}>
            <Icon name="arrowLeft" size={16} /> Go Back
          </button>
        </div>
      </div>
    );
  }

  const initial = agent ? (agent.agent_name ?? 'A')[0].toUpperCase() : 'A';
  const waUrl = agent ? whatsappUrl(
    agent.agent_phone_number,
    `Hi ${agent.agent_name}, I found your profile on Weespas and would like to inquire about your properties.`,
  ) : '#';

  return (
    <div className="ap-page">
      <PageMeta
        title={agent ? `${agent.agent_name} — Agent Profile` : 'Agent Profile'}
        description={agent?.bio ?? 'View this agent\'s property listings on Weespas.'}
      />

      {/* Back button */}
      <button type="button" className="ap-back" onClick={handleGoBack}>
        <Icon name="arrowLeft" size={18} />
        <span>Back</span>
      </button>

      {/* Profile Section */}
      {agentLoading ? (
        <SkeletonProfile />
      ) : agent ? (
        <section className="ap-profile">
          <div className="ap-profile__avatar-wrap">
            <div className="ap-profile__avatar">
              {agent.agent_profile_picture ? (
                <img
                  src={resolveMediaUrl(agent.agent_profile_picture)}
                  alt={agent.agent_name}
                  loading="eager"
                />
              ) : (
                <span className="ap-profile__initial">{initial}</span>
              )}
            </div>
            {agent.is_verified && (
              <span className="ap-profile__verified" title="Verified agent">
                <Icon name="verified" size={18} />
              </span>
            )}
          </div>

          <div className="ap-profile__details">
            <h1 className="ap-profile__name">{agent.agent_name}</h1>
            {agent.email && (
              <span className="ap-profile__email">{agent.email}</span>
            )}
            {agent.bio && (
              <p className="ap-profile__bio">{linkify(agent.bio)}</p>
            )}
            <div className="ap-profile__stats">
              <div className="ap-profile__stat">
                <strong>{agent.property_count}</strong>
                <span>{agent.property_count === 1 ? 'Listing' : 'Listings'}</span>
              </div>
            </div>
          </div>

          {/* Action Buttons */}
          <div className="ap-profile__actions">
            <a
              href={`tel:${agent.agent_phone_number}`}
              className="ap-action-btn ap-action-btn--call"
            >
              <Icon name="phone" size={18} />
              <span>Call</span>
            </a>
            <a
              href={waUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="ap-action-btn ap-action-btn--whatsapp"
            >
              <Icon name="whatsapp" size={18} />
              <span>WhatsApp</span>
            </a>
            <button type="button" disabled className="ap-action-btn ap-action-btn--chat">
              <Icon name="chat" size={18} />
              <span>Chat</span>
              <span className="ap-action-btn__badge">Soon</span>
            </button>
            <button type="button" disabled className="ap-action-btn ap-action-btn--video">
              <Icon name="videoCall" size={18} />
              <span>Video</span>
              <span className="ap-action-btn__badge">Soon</span>
            </button>
          </div>
        </section>
      ) : null}

      {/* Short Videos Section */}
      {allVideos.length > 0 && (
        <section className="ap-shorts">
          <div className="ap-shorts__header">
            <h2 className="ap-shorts__title">
              <Icon name="video" size={20} />
              Short Videos
            </h2>
            <span className="ap-shorts__count">{allVideos.length} {allVideos.length === 1 ? 'video' : 'videos'}</span>
          </div>
          <p className="ap-shorts__subtitle">Quick property tours and updates from this agent</p>
          <div className="ap-shorts__scroll">
            <div className="ap-shorts__track">
              {allVideos.map((video) => (
                <ShortVideoCard
                  key={video.id}
                  video={video}
                  onPlay={setVideoPlayerOpen}
                />
              ))}
            </div>
          </div>
        </section>
      )}

      {/* Property Listings */}
      <section className="ap-listings" ref={listingsRef}>
        <div className="ap-listings__header">
          <h2 className="ap-listings__title">
            <Icon name="home" size={20} />
            Properties
          </h2>
          <span className="ap-listings__count">
            {totalProperties} {totalProperties === 1 ? 'listing' : 'listings'}
          </span>
        </div>

        {propsLoading ? (
          <div className="ap-listings__grid">
            {Array.from({ length: 6 }).map((_, i) => (
              <SkeletonPropertyCard key={i} />
            ))}
          </div>
        ) : properties.length === 0 ? (
          <div className="ap-listings__empty">
            <Icon name="home" size={40} />
            <p>No properties listed yet.</p>
          </div>
        ) : (
          <>
            <div className="ap-listings__grid">
              {properties.map((property) => (
                <AgentPropertyCard
                  key={property.id}
                  property={property}
                  onSelect={handlePropertySelect}
                />
              ))}
            </div>
            <Pagination
              currentPage={currentPage}
              totalPages={totalPages}
              onPageChange={handlePageChange}
            />
          </>
        )}
      </section>

      <RelatedProperties
        properties={relatedProperties}
        isLoading={relatedLoading}
        onSelect={handlePropertySelect}
        title="Other Related Properties"
      />

      {/* Property Details Panel — portalled to body to escape PageTransition transform context */}
      {selectedProperty && createPortal(
        <PropertyDetailsById
          propertyId={selectedProperty.id}
          fallbackProperty={selectedProperty}
          onClose={() => setSelectedProperty(null)}
        />,
        document.body,
      )}

      {/* Video Player Modal */}
      {videoPlayerOpen && (
        <div
          className="ap-video-modal"
          onClick={() => setVideoPlayerOpen(null)}
        >
          <div
            className="ap-video-modal__content"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className="ap-video-modal__close"
              onClick={() => setVideoPlayerOpen(null)}
              aria-label="Close video"
            >
              <Icon name="x" size={22} />
            </button>
            <video
              ref={videoRef}
              src={videoPlayerOpen}
              controls
              autoPlay
              playsInline
              className="ap-video-modal__player"
            >
              Your browser does not support video playback.
            </video>
          </div>
        </div>
      )}
    </div>
  );
};

export default AgentProfilePage;
