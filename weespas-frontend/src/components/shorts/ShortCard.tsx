// src/components/shorts/ShortCard.tsx
import React from 'react';
import Icon from '../ui/Icon';
import DismissButton from '../ui/DismissButton';
import ListingTypeBadge from '../ui/ListingTypeBadge';
import { formatPrice } from '../../utils/format';
import { resolveMediaUrl } from '../../utils/media';
import type { PropertyShort } from '../../api/shorts';
import './ShortCard.css';

interface ShortCardProps {
  short: PropertyShort;
  onSelect: (id: string) => void;
  onDismiss?: (id: string) => void;
  /** Show the FOR SALE/FOR RENT badge (real-estate). Off for commerce shop videos. Default on. */
  showListingBadge?: boolean;
  /** Override the price line. Defaults to the real-estate formatPrice; commerce passes its own. */
  priceLabel?: string;
  /** When there's no poster image, show the video's first frame as the tile still (a muted,
   *  metadata-preloaded <video> — no autoplay). Used by commerce shop videos, which carry no
   *  generated thumbnail. Default off (real-estate shorts always have a poster). */
  posterFromVideo?: boolean;
}

const ShortCard: React.FC<ShortCardProps> = ({
  short,
  onSelect,
  onDismiss,
  showListingBadge = true,
  priceLabel,
  posterFromVideo = false,
}) => {
  const poster = resolveMediaUrl(short.video.thumbnail_url || short.main_image?.thumbnail_url || short.main_image?.url);
  // Fall back to the video's first frame when there's no image poster (commerce path).
  const videoPoster = posterFromVideo && !poster
    ? resolveMediaUrl(short.video.streaming_url || short.video.url)
    : undefined;

  return (
    <div
      className="short-card"
      role="button"
      tabIndex={0}
      onClick={() => onSelect(short.id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect(short.id);
        }
      }}
    >
      {onDismiss && <DismissButton onDismiss={() => onDismiss(short.id)} />}
      <div className="short-card__poster">
        {poster ? (
          <img src={poster} alt={short.title} loading="lazy" />
        ) : videoPoster ? (
          // First-frame still — muted/preload=metadata, never autoplays. The shelf is a poster wall;
          // playback happens in the vertical feed on tap.
          <video
            className="short-card__video-still"
            src={videoPoster}
            muted
            playsInline
            preload="metadata"
            tabIndex={-1}
            aria-hidden="true"
          />
        ) : (
          <span className="short-card__poster-placeholder">{short.title.slice(0, 1)}</span>
        )}
        {/* Same component the normal image card uses, same top-left
            anchor — keeps the rent/sale signal positioned consistently
            across surfaces. */}
        {showListingBadge && (
          <ListingTypeBadge type={short.listing_type} className="short-card__listing-badge" />
        )}
        <div className="short-card__play" aria-hidden="true">
          <Icon name="play" size={28} />
        </div>
        {/* Title + price overlay the poster — mirrors the Vertical Video
            Scroll info block. Single shared scrim renders behind both lines
            so we pay one gradient instead of two. Title sits above the
            price (same order as the vertical feed) and clamps to 2 lines. */}
        <div className="short-card__overlay">
          <p className="short-card__title" title={short.title}>{short.title}</p>
          <p className="short-card__price">{priceLabel ?? formatPrice(short.price, short.currency, short.listing_type)}</p>
        </div>
      </div>
    </div>
  );
};

export default ShortCard;
