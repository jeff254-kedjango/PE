/* ==========================================================================
   PROPERTY DETAILS — Full property view
   Mobile: slide-up bottom sheet.  Desktop: right side panel.
   Includes image carousel with Stories dots, specs grid, agent card, etc.
   ========================================================================== */

import React, { useState, useCallback, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Property, PropertyImage } from '../../types/propertyApi';
import { useFavorites } from '../../hooks/useFavorites';
import { formatPrice, formatDistance, getVibeTags } from '../../utils/format';
import { resolveMediaUrl } from '../../utils/media';
import Icon from '../ui/Icon';
import FavoriteButton from '../ui/FavoriteButton';
import ListingTypeBadge from '../ui/ListingTypeBadge';
import VerifiedBadge from '../ui/VerifiedBadge';
import VibeTag from '../ui/VibeTag';
import Badge from '../ui/Badge';
import ImageGallery from '../ui/ImageGallery';
import PropertyLocationMap from '../map/PropertyLocationMap';
import RiskPill from './RiskPill';
import StructuralFlagModal from './StructuralFlagModal';
import BuildingConfirmModal from './BuildingConfirmModal';
import { useReveal } from '../../context/RevealContext';
import { useAuth } from '../../context/AuthContext';
import { useListingRisk } from '../../hooks/useListingRisk';
import { openInsarRiskMap } from '../../api/insar';
import { isCertifier } from '../../utils/roles';
import './PropertyDetails.css';

interface PropertyDetailsProps {
  property: Property;
  onClose: () => void;
}

const PropertyDetails: React.FC<PropertyDetailsProps> = ({ property, onClose }) => {
  const [currentSlide, setCurrentSlide] = useState(0);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [videoPlayerOpen, setVideoPlayerOpen] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  // Ref on the scrollable panel so ArrowUp/Down can drive its scroll directly,
  // even when PropertyDetails layers over a scroll-locked parent (the shorts
  // feed). Without this, the panel inherits keyboard focus visually but the
  // arrow keys still scroll whatever element had focus before it opened.
  const panelRef = useRef<HTMLDivElement | null>(null);
  const { isFavorite, toggleFavorite } = useFavorites();
  const { requestReveal, getRevealed } = useReveal();
  const { token, user } = useAuth();
  const navigate = useNavigate();
  const [revealing, setRevealing] = useState(false);
  const [flagOpen, setFlagOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  // Gather all images — main image first, then extras
  const allImages: PropertyImage[] = [];
  if (property.main_image) allImages.push(property.main_image);
  if (property.images) {
    for (const img of property.images) {
      if (img.id !== property.main_image?.id) allImages.push(img);
    }
  }
  const imageCount = allImages.length;

  // Keyboard navigation. ArrowLeft/Right move the carousel, ArrowUp/Down
  // scroll the panel body, Escape closes. We use `capture: true` so this
  // runs BEFORE any feed underneath us — when PropertyDetails is on top,
  // it owns the keyboard, full stop. e.preventDefault() then stops the
  // browser's default scroll on the locked body.
  //
  // Smoothness model: we DO NOT call `scrollBy({behavior:'smooth'})` per
  // keypress. The OS repeats keydown at ~30/s when held, and each smooth
  // scroll cancels the previous one — the visible result is a stutter
  // ("bouncing") as overlapping animations restart toward shifting targets.
  // Instead we maintain a single `targetTopRef` and lerp `scrollTop` toward
  // it inside one rAF loop. Repeated keypresses simply bump the target;
  // the animation keeps converging without restarting. One rAF loop, one
  // scrollTop write per frame — strictly cheaper than the browser's
  // smooth-scroll machinery, and stutter-free on held keys.
  useEffect(() => {
    const SCROLL_STEP = 120;             // pixels per Arrow keypress
    const SCROLL_PAGE_RATIO = 0.85;      // PageUp/PageDown moves ~one viewport
    // Lerp factor per frame. ~0.22 reaches the target in ~12 frames at 60fps
    // (≈200ms), which feels native — matches the browser's own smooth-scroll
    // cadence without the per-event restart artifact.
    const LERP = 0.22;
    const SETTLE_EPSILON = 0.5;          // px — snap when close enough to stop rAF

    let targetTop: number | null = null;
    let rafId = 0;

    const tick = () => {
      const panel = panelRef.current;
      if (!panel || targetTop === null) { rafId = 0; return; }
      const current = panel.scrollTop;
      const delta = targetTop - current;
      if (Math.abs(delta) < SETTLE_EPSILON) {
        panel.scrollTop = targetTop;
        targetTop = null;
        rafId = 0;
        return;
      }
      panel.scrollTop = current + delta * LERP;
      rafId = requestAnimationFrame(tick);
    };

    // Push the target by `delta` px and ensure the rAF loop is running.
    // The current `scrollTop` (not the in-flight target) is the base only
    // when no animation is active — otherwise we stack onto the pending
    // target so rapid presses accumulate cleanly instead of being lost.
    const queueScrollBy = (delta: number) => {
      const panel = panelRef.current;
      if (!panel) return;
      const max = panel.scrollHeight - panel.clientHeight;
      const base = targetTop ?? panel.scrollTop;
      targetTop = Math.max(0, Math.min(max, base + delta));
      if (!rafId) rafId = requestAnimationFrame(tick);
    };

    const queueScrollTo = (top: number) => {
      const panel = panelRef.current;
      if (!panel) return;
      const max = panel.scrollHeight - panel.clientHeight;
      targetTop = Math.max(0, Math.min(max, top));
      if (!rafId) rafId = requestAnimationFrame(tick);
    };

    const handleKey = (e: KeyboardEvent) => {
      // Never hijack typing.
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;

      if (e.key === 'Escape') { onClose(); return; }
      if (e.key === 'ArrowLeft' && currentSlide > 0) {
        e.preventDefault();
        setCurrentSlide((s) => s - 1);
        return;
      }
      if (e.key === 'ArrowRight' && currentSlide < imageCount - 1) {
        e.preventDefault();
        setCurrentSlide((s) => s + 1);
        return;
      }

      const panel = panelRef.current;
      if (!panel) return;
      if (e.key === 'ArrowDown' || e.key === 'j') {
        e.preventDefault();
        queueScrollBy(SCROLL_STEP);
      } else if (e.key === 'ArrowUp' || e.key === 'k') {
        e.preventDefault();
        queueScrollBy(-SCROLL_STEP);
      } else if (e.key === 'PageDown' || e.key === ' ') {
        e.preventDefault();
        queueScrollBy(panel.clientHeight * SCROLL_PAGE_RATIO);
      } else if (e.key === 'PageUp') {
        e.preventDefault();
        queueScrollBy(-panel.clientHeight * SCROLL_PAGE_RATIO);
      } else if (e.key === 'Home') {
        e.preventDefault();
        queueScrollTo(0);
      } else if (e.key === 'End') {
        e.preventDefault();
        queueScrollTo(panel.scrollHeight);
      }
    };
    // capture phase: runs before VerticalVideoFeed's listener even if both
    // are mounted. Belt-and-suspenders alongside the feed's `.pd-panel` guard.
    window.addEventListener('keydown', handleKey, true);
    return () => {
      window.removeEventListener('keydown', handleKey, true);
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, [onClose, currentSlide, imageCount]);

  // Lock body scroll while panel is open
  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, []);

  // Free network + decoder for the panel's images. The shorts feed leaves
  // the active video streaming behind us; on HTTP/1.1 that holds one of
  // the ~6 per-origin connection slots indefinitely and on any transport
  // it monopolises one hardware decoder slice on mobile. Both delay the
  // first paint of our carousel image — exactly the symptom the user saw
  // (images load only after they scroll, because the video is then
  // paused by the feed's IntersectionObserver going off-screen).
  //
  // We pause every <video> in the document on mount. When the panel
  // unmounts, the shorts feed's `isActive` effect re-issues play() on
  // whichever short is still active — no cross-component wiring needed,
  // single source of truth (feed-side `isActive`) preserved.
  useEffect(() => {
    const playing: HTMLVideoElement[] = [];
    const videos = document.querySelectorAll<HTMLVideoElement>('video');
    for (let i = 0; i < videos.length; i++) {
      const v = videos[i];
      if (!v.paused && !v.ended) {
        v.pause();
        playing.push(v);
      }
    }
    return () => {
      // Best-effort resume. The shorts feed will also call play() via its
      // own effect when it regains focus; either path is idempotent.
      for (let i = 0; i < playing.length; i++) {
        const p = playing[i].play();
        if (p && typeof p.catch === 'function') p.catch(() => { /* ignored */ });
      }
    };
  }, []);

  const prevSlide = useCallback(() => setCurrentSlide((s) => Math.max(0, s - 1)), []);
  const nextSlide = useCallback(() => setCurrentSlide((s) => Math.min(imageCount - 1, s + 1)), [imageCount]);

  const handleShare = async () => {
    const url = window.location.origin + '/property/' + property.id;
    if (navigator.share) {
      try { await navigator.share({ title: property.title, url }); } catch { /* user cancelled */ }
    } else {
      await navigator.clipboard.writeText(url);
    }
  };

  // Build specs list
  const specs: { icon: React.ComponentProps<typeof Icon>['name']; value: string; label: string }[] = [];
  if (property.bedrooms != null) specs.push({ icon: 'bed', value: String(property.bedrooms), label: 'Beds' });
  if (property.bathrooms != null) specs.push({ icon: 'bath', value: String(property.bathrooms), label: 'Baths' });
  if (property.size) specs.push({ icon: 'ruler', value: property.size, label: 'Size' });
  if (property.parking_spaces != null) specs.push({ icon: 'parking', value: String(property.parking_spaces), label: 'Parking' });
  if (property.year_built) specs.push({ icon: 'calendar', value: String(property.year_built), label: 'Built' });

  const vibeTags = getVibeTags(property);

  // Agent helpers
  const agent = property.agent;
  const agentInitial = (agent?.agent_name ?? 'A')[0].toUpperCase();
  const agentPhone = agent?.agent_phone_number;
  const whatsappUrl = agentPhone ? `https://wa.me/${agentPhone.replace(/\D/g, '')}?text=${encodeURIComponent(`Hi, I'm interested in: ${property.title}`)}` : null;

  // Location text
  const locationParts: string[] = [];
  if (property.address?.street_address) locationParts.push(property.address.street_address);
  if (property.address?.location_name ?? property.location_name) locationParts.push((property.address?.location_name ?? property.location_name)!);
  if (property.address?.city) locationParts.push(property.address.city);
  if (property.address?.county) locationParts.push(property.address.county);
  const locationText = locationParts.join(', ') || 'Kenya';

  // Resolve coordinates. NOTE: anything the list/detail API returns is intentionally
  // FUZZED (~1km blur, server-side — billing_architecture §6). Exact coords arrive
  // ONLY after a paid reveal, which we cache in RevealContext. So we prefer the
  // revealed coords when present, and fall back to the fuzzed ones for the map.
  const revealed = getRevealed(property.id);
  const fuzzLat = property.latitude ?? property.address?.latitude;
  const fuzzLng = property.longitude ?? property.address?.longitude;
  const lat = revealed?.latitude ?? fuzzLat;
  const lng = revealed?.longitude ?? fuzzLng;
  const isRevealed = revealed != null;

  // "Get directions" — reveal-gated. Opens the chooser (M-Pesa) when needed; once
  // revealed, opens the device's map app with the EXACT destination.
  const handleGetDirections = useCallback(async () => {
    if (revealing) return;
    setRevealing(true);
    try {
      const coords = await requestReveal(property.id);
      if (coords) window.open(coords.directions_url, '_blank', 'noopener,noreferrer');
    } finally {
      setRevealing(false);
    }
  }, [revealing, requestReveal, property.id]);

  // "View Building Risk Analysis" — opens the InSAR map deep-linked to THIS listing's building
  // (the backend resolves listing→footprint). InSAR is free but login-required: authed
  // users open it in a new tab with a telemetry token (the view is metered); anon users
  // are routed to login and resume on this building after sign-in.
  const handleViewRiskMap = useCallback(() => {
    void openInsarRiskMap(token, navigate, property.id);
  }, [token, navigate, property.id]);

  // InSAR subsidence coverage for this listing (work_flow.md §9.3 Option B). The
  // backend resolves listing→footprint→tier (3-state, never "unknown→safe"); the
  // pill renders it honestly. Slow-moving signal, long-cached in the hook.
  const { data: listingRisk, isLoading: riskLoading, isError: riskError } =
    useListingRisk(property.id);
  // A freshly-uploaded listing whose footprint check hasn't finished shows
  // "Verifying…" instead of a (not-yet-meaningful) coverage reading.
  const verifying = property.verification_status === 'pending';
  // A certifier (engineer/authority/staff/admin) can record a structural judgement
  // for a MONITORED building — we need its resolved aoi + building id to target it.
  const canFlag =
    isCertifier(user) &&
    listingRisk?.coverage === 'monitored' &&
    !!listingRisk.aoi_code &&
    listingRisk.insar_building_id != null;

  // The listing OWNER can tap-to-confirm which building a clustered pin really is. Only
  // the owning agent (or an admin) gets the confirm CTA — others just see the provisional
  // pill. Ownership = the viewer's agent_id matches the listing's agent.
  const isOwner =
    !!user &&
    ((!!user.agent_id && user.agent_id === property.agent?.id) ||
      user.role === 'admin' ||
      (Array.isArray(user.roles) && user.roles.includes('admin')));
  const canConfirm = isOwner && listingRisk?.coverage === 'needs_confirmation';

  // Only a MONITORED listing resolves to a single building the InSAR map can analyze; for
  // every other coverage state (not_monitored / needs_confirmation / monitored_land /
  // unavailable, and while still verifying) the deep-link carries no building to focus, so
  // the "View Building Risk Analysis" entry would lead to a map that can't show this
  // building — hide it. Only monitored buildings appear in our risk map. (work_flow §9.3)
  const canViewRiskMap = listingRisk?.coverage === 'monitored';

  // Deep-link from the verification notification ("/properties/{id}?confirm=1") opens the
  // confirm flow straight away — but only for the owner, and only once candidates exist.
  useEffect(() => {
    if (!canConfirm) return;
    const params = new URLSearchParams(window.location.search);
    if (params.get('confirm') === '1') setConfirmOpen(true);
  }, [canConfirm]);

  return (
    <>
      {/* Backdrop */}
      <div className="pd-overlay" onClick={onClose} />

      {/* Panel */}
      <div className="pd-panel" role="dialog" aria-label={property.title} ref={panelRef}>
        {/* Mobile drag handle */}
        <div className="pd-handle"><span /></div>

        {/* Close button */}
        <button className="pd-close" onClick={onClose} aria-label="Close">
          <Icon name="x" size={18} />
        </button>

        {/* ── Image Carousel ── */}
        {imageCount > 0 ? (
          <div className="pd-carousel">
            {/* Stories progress dots */}
            {imageCount > 1 && (
              <div className="pd-carousel__dots">
                {allImages.map((_, i) => (
                  <div
                    key={i}
                    className={`pd-carousel__dot ${i === currentSlide ? 'pd-carousel__dot--active' : i < currentSlide ? 'pd-carousel__dot--done' : ''}`}
                  />
                ))}
              </div>
            )}

            {/* Track — click opens fullscreen lightbox.
                Slide loading tier mirrors ShortItem's pattern: the active
                slide is eager + high-priority (the user is staring at it),
                its immediate neighbours are eager so Arrow-Left/Right is
                instant, and the rest are lazy. `loading="lazy"` on the
                active slide was the bug: when the panel animates in from
                the right, the browser's intersection observer fires
                before the slide has finished transitioning into the
                viewport, so the fetch was getting deferred until the
                user scrolled the panel and re-triggered intersection.
                `decoding="async"` keeps image decode off the main thread. */}
            <div className="pd-carousel__track" style={{ transform: `translateX(-${currentSlide * 100}%)` }}>
              {allImages.map((img, i) => {
                const distance = Math.abs(i - currentSlide);
                const isActive = distance === 0;
                const isNear = distance <= 1;
                return (
                  <div
                    key={img.id}
                    className="pd-carousel__slide"
                    onClick={() => setLightboxOpen(true)}
                    role="button"
                    tabIndex={0}
                    aria-label="Open fullscreen gallery"
                  >
                    <img
                      src={resolveMediaUrl(img.url || img.thumbnail_url)}
                      alt={img.alt_text ?? property.title}
                      loading={isNear ? 'eager' : 'lazy'}
                      decoding="async"
                      // fetchpriority lets the active slide jump ahead of the
                      // background video stream still draining a connection
                      // slot from the shorts feed. Cast: the attr is in the
                      // spec but not yet in React's DOM types.
                      {...({ fetchpriority: isActive ? 'high' : 'auto' } as Record<string, string>)}
                    />
                  </div>
                );
              })}
            </div>

            {/* Arrows */}
            {currentSlide > 0 && (
              <button className="pd-carousel__arrow pd-carousel__arrow--prev" onClick={prevSlide} aria-label="Previous image">
                <Icon name="chevronLeft" size={18} />
              </button>
            )}
            {currentSlide < imageCount - 1 && (
              <button className="pd-carousel__arrow pd-carousel__arrow--next" onClick={nextSlide} aria-label="Next image">
                <Icon name="chevronRight" size={18} />
              </button>
            )}

            {/* Glassmorphic price overlay */}
            {property.price != null && (
              <div className="pd-price-overlay">
                <span className="pd-price-overlay__amount">{formatPrice(property.price, property.currency, property.listing_type)}</span>
              </div>
            )}

            {/* Image counter */}
            {imageCount > 1 && (
              <span className="pd-carousel__counter">{currentSlide + 1} / {imageCount}</span>
            )}

            {/* Expand hint */}
            <button
              className="pd-carousel__expand"
              onClick={() => setLightboxOpen(true)}
              aria-label="Open fullscreen gallery"
            >
              <Icon name="expand" size={16} />
            </button>
          </div>
        ) : (
          /* No-image fallback */
          <div className="pd-carousel" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'rgba(255,255,255,0.4)' }}>
            <Icon name="expand" size={48} />
          </div>
        )}

        {/* ── Body content ── */}
        <div className="pd-body">
          {/* Header: badges + title + location */}
          <div className="pd-header">
            <div className="pd-header__badges">
              <ListingTypeBadge type={property.listing_type} />
              {property.is_engineer_certified && <VerifiedBadge size={18} />}
              {property.distance != null && (
                <Badge variant="accent">{formatDistance(property.distance)}</Badge>
              )}
              {property.category && <Badge variant="muted">{property.category}</Badge>}
            </div>
            <h2 className="pd-header__title">{property.title}</h2>
            <div className="pd-header__location">
              <Icon name="mapPin" size={14} />
              <span>{locationText}</span>
            </div>
          </div>

          {/* Price (fallback if no images to show overlay) */}
          {property.price != null && imageCount === 0 && (
            <div>
              <span className="pd-price-overlay__amount" style={{ color: 'var(--color-text)' }}>
                {formatPrice(property.price, property.currency, property.listing_type)}
              </span>
            </div>
          )}

          {/* Actions: Share + Favorite */}
          <div className="pd-actions">
            <button className="pd-actions__share" onClick={handleShare}>
              <Icon name="share" size={16} />
              Share
            </button>
            <FavoriteButton
              active={isFavorite(property.id)}
              onToggle={() => toggleFavorite(property.id)}
            />
          </div>

          {/* Vibe tags */}
          {vibeTags.length > 0 && (
            <div className="pd-vibes">
              {vibeTags.map((tag) => <VibeTag key={tag} tag={tag} />)}
            </div>
          )}

          <hr className="pd-divider" />

          {/* Specs grid */}
          {specs.length > 0 && (
            <div className="pd-specs">
              {specs.map((spec) => (
                <div key={spec.label} className="pd-spec">
                  <Icon name={spec.icon} size={20} className="pd-spec__icon" />
                  <span className="pd-spec__value">{spec.value}</span>
                  <span className="pd-spec__label">{spec.label}</span>
                </div>
              ))}
            </div>
          )}

          {/* Description */}
          {property.description && (
            <div className="pd-description">
              <h3>About this property</h3>
              <p>{property.description}</p>
            </div>
          )}

          {/* Video Tour */}
          {property.videos && property.videos.length > 0 && (
            <>
              <hr className="pd-divider" />
              <div className="pd-video-tour">
                <h3>
                  <Icon name="video" size={18} />
                  Video Tour
                </h3>
                <div className="pd-video-tour__grid">
                  {property.videos.map((video) => {
                    const src = resolveMediaUrl(video.streaming_url || video.url)!;
                    return (
                      <button
                        key={video.id}
                        type="button"
                        className="pd-video-tour__thumb"
                        onClick={() => setVideoPlayerOpen(src)}
                      >
                        {video.thumbnail_url ? (
                          <img src={resolveMediaUrl(video.thumbnail_url)} alt={video.title ?? 'Video tour'} loading="lazy" />
                        ) : (
                          <div className="pd-video-tour__thumb-fallback">
                            <Icon name="video" size={28} />
                          </div>
                        )}
                        <div className="pd-video-tour__play-overlay">
                          <div className="pd-video-tour__play-btn">
                            <Icon name="play" size={20} />
                          </div>
                        </div>
                        {video.title && (
                          <span className="pd-video-tour__title">{video.title}</span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            </>
          )}

          <hr className="pd-divider" />

          {/* Location */}
          <div className="pd-location">
            <div className="pd-location__head">
              <h3>Location</h3>
              <RiskPill risk={listingRisk} isLoading={riskLoading} isError={riskError} isPending={verifying} />
              {canFlag && (
                <button
                  type="button"
                  className="pd-flag-btn"
                  onClick={() => setFlagOpen(true)}
                  title="Record a structural judgement for this building"
                >
                  <Icon name="verified" size={14} />
                  Flag building
                </button>
              )}
            </div>
            <p className="pd-location__address">{locationText}</p>
            {canConfirm && (
              <button
                type="button"
                className="pd-confirm-cta"
                onClick={() => setConfirmOpen(true)}
              >
                <Icon name="mapPin" size={16} />
                <span>
                  <strong>Confirm your building</strong>
                  <small>This pin is near a few monitored buildings — tap to pick the right one.</small>
                </span>
                <Icon name="chevronRight" size={16} />
              </button>
            )}
            {lat != null && lng != null ? (
              <>
                <PropertyLocationMap
                  latitude={lat}
                  longitude={lng}
                  address={locationText}
                />
                {!isRevealed && (
                  <p className="pd-location__approx">
                    <Icon name="mapPin" size={13} />
                    Approximate area shown. Unlock to see the exact spot and get directions.
                  </p>
                )}
                <button
                  type="button"
                  className="pd-location__directions"
                  onClick={handleGetDirections}
                  disabled={revealing}
                >
                  <Icon name="mapPin" size={16} />
                  {revealing ? 'Unlocking…' : isRevealed ? 'Get Directions' : 'Unlock & Get Directions'}
                </button>
                {canViewRiskMap && (
                  <button
                    type="button"
                    className="pd-location__risk-map"
                    onClick={handleViewRiskMap}
                  >
                    <Icon name="map" size={16} />
                    View Building Risk Analysis
                  </button>
                )}
              </>
            ) : (
              <div className="pd-location__map-placeholder">
                <Icon name="map" size={24} />
                <span style={{ marginLeft: '8px' }}>Map not available</span>
              </div>
            )}
          </div>

          <hr className="pd-divider" />

          {/* Agent card */}
          {agent && (
            <div>
              <div className="pd-agent">
                {agent.agent_profile_picture ? (
                  <img className="pd-agent__photo" src={resolveMediaUrl(agent.agent_profile_picture)} alt={agent.agent_name ?? 'Agent'} />
                ) : (
                  <div className="pd-agent__photo pd-agent__photo--placeholder">{agentInitial}</div>
                )}
                <div className="pd-agent__info">
                  <div className="pd-agent__name">{agent.agent_name ?? 'Agent'}</div>
                  <div className="pd-agent__role">Property Agent{agent.is_verified ? ' \u00b7 Verified' : ''}</div>
                </div>
              </div>
              <div className="pd-agent__ctas">
                {agentPhone && (
                  <a className="pd-agent__cta pd-agent__cta--call" href={`tel:${agentPhone}`}>
                    <Icon name="phone" size={16} />
                    Call
                  </a>
                )}
                {whatsappUrl && (
                  <a className="pd-agent__cta pd-agent__cta--whatsapp" href={whatsappUrl} target="_blank" rel="noopener noreferrer">
                    WhatsApp
                  </a>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Fullscreen lightbox */}
      {lightboxOpen && imageCount > 0 && (
        <ImageGallery
          images={allImages}
          initialIndex={currentSlide}
          onClose={() => setLightboxOpen(false)}
        />
      )}

      {/* Video player modal */}
      {videoPlayerOpen && (
        <div className="pd-video-modal" onClick={() => { setVideoPlayerOpen(null); }}>
          <div className="pd-video-modal__content" onClick={(e) => e.stopPropagation()}>
            <button
              className="pd-video-modal__close"
              onClick={() => setVideoPlayerOpen(null)}
              aria-label="Close video"
            >
              <Icon name="x" size={20} />
            </button>
            <video
              ref={videoRef}
              src={resolveMediaUrl(videoPlayerOpen)}
              controls
              autoPlay
              playsInline
              className="pd-video-modal__player"
            >
              Your browser does not support video playback.
            </video>
          </div>
        </div>
      )}

      {canFlag && (
        <StructuralFlagModal
          isOpen={flagOpen}
          listingId={property.id}
          aoiCode={listingRisk!.aoi_code as string}
          insarBuildingId={listingRisk!.insar_building_id as number}
          onClose={() => setFlagOpen(false)}
        />
      )}

      {/* Gate the MOUNT on confirmOpen alone, not canConfirm: confirmOpen is only ever set
          under a canConfirm check (the CTA renders and the deep-link effect fires only when
          canConfirm), so authorization is unchanged. But a successful confirm flips coverage
          monitored→canConfirm=false; gating on canConfirm here would unmount the modal the
          instant the risk refetch lands, cutting off its 1.6s "confirmed!" success screen.
          Letting the modal own its lifecycle (it closes itself via onClose) keeps that intact. */}
      {confirmOpen && (
        <BuildingConfirmModal
          listingId={property.id}
          onClose={() => setConfirmOpen(false)}
        />
      )}
    </>
  );
};

export default PropertyDetails;
