// src/components/shorts/ShortItem.tsx
import React, { useEffect, useRef } from 'react';
import Icon from '../ui/Icon';
import { formatPrice } from '../../utils/format';
import { resolveMediaUrl } from '../../utils/media';
import { recordShortView } from '../../utils/shortsAnalytics';
import { useIsFavorite, useToggleFavorite } from '../../hooks/useFavorites';
import type { PropertyShort } from '../../api/shorts';
import './ShortItem.css';

/** Cumulative seconds of active playback before a short counts as "watched". */
const WATCH_THRESHOLD_MS = 3000;

export type PreloadTier = 'auto' | 'metadata' | 'none';

interface ShortItemBaseProps {
  short: PropertyShort;
  index: number;
  isActive: boolean;
  preload: PreloadTier;
  register: (index: number, node: HTMLElement | null) => void;
  onSelect: (id: string) => void;
  onDismiss?: (id: string) => void;
  /** Whether the like/heart is filled. Controlled by the parent (favorites store OR commerce save). */
  liked: boolean;
  /** Toggle the like for this id (favorites store on home; commerce save on Trade). */
  onToggleLike: (id: string) => void;
  /** Override the price line. Defaults to the real-estate formatPrice. */
  priceLabel?: string;
  /** Called once when the short crosses the watch threshold. Defaults to real-estate analytics. */
  onWatched?: (id: string) => void;
}

/**
 * One viewport-height card in the vertical Shorts feed — PURE/controlled.
 *
 * Like-state and the watch callback are injected so the same item renders for both the real-estate
 * shorts feed (favorites store + property analytics) and the commerce Trade feed (save toggle).
 * Use the default `ShortItem` export below for the real-estate path; commerce passes its own
 * `liked`/`onToggleLike`/`priceLabel`/`onWatched`.
 *
 * Performance notes:
 * - Parent decides which item is "active". We only call play()/pause() and
 *   thrash `preload` when those props change — no observers per item.
 * - Inactive videos reset to currentTime=0 to free decoder memory on mobile.
 * - Poster image is always rendered for instant first paint; the <video>
 *   element only attaches its src URL when the item is in the visible window
 *   (preload !== 'none'), avoiding speculative HEAD/range requests.
 * - `playsInline muted` is mandatory for iOS Safari autoplay.
 */
const ShortItemBase: React.FC<ShortItemBaseProps> = ({
  short,
  index,
  isActive,
  preload,
  register,
  onSelect,
  onDismiss,
  liked,
  onToggleLike,
  priceLabel,
  onWatched = recordShortView,
}) => {
  const containerRef = useRef<HTMLElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const isFav = liked;
  const toggleFavorite = onToggleLike;

  // Register this DOM node with the parent's shared IntersectionObserver.
  useEffect(() => {
    register(index, containerRef.current);
    return () => register(index, null);
  }, [register, index]);

  // Drive playback from the `isActive` prop only — no internal observer.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    if (isActive) {
      const playPromise = v.play();
      // Safari may reject if user hasn't interacted; muted+playsInline should let it through.
      if (playPromise && typeof playPromise.catch === 'function') {
        playPromise.catch(() => { /* autoplay blocked; user can tap to retry */ });
      }
    } else {
      v.pause();
      // Reset so seeking back later starts cleanly and the decoder can release.
      try { v.currentTime = 0; } catch { /* readyState may be 0 */ }
    }
  }, [isActive]);

  // ── Watch-time tracking ─────────────────────────────────────────────────
  // We accumulate "actively-playing" milliseconds; once we cross the
  // threshold we record one view (via the shared dedup'd tracker, so the
  // same property can't be counted twice in a session even across remounts).
  // We use a rAF-less timestamp diff approach — start tick when playing,
  // stop tick when pausing/leaving — so no per-frame work runs.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    let watchedMs = 0;
    let startedAt = 0;
    let fired = false;

    const onPlay = () => {
      if (fired) return;
      startedAt = performance.now();
    };
    const onPauseOrEnd = () => {
      if (fired || startedAt === 0) { startedAt = 0; return; }
      watchedMs += performance.now() - startedAt;
      startedAt = 0;
      if (watchedMs >= WATCH_THRESHOLD_MS) {
        fired = true;
        onWatched(short.id);
      }
    };
    v.addEventListener('play', onPlay);
    v.addEventListener('pause', onPauseOrEnd);
    v.addEventListener('ended', onPauseOrEnd);
    return () => {
      // Flush the currently-running interval before cleanup so a fast
      // unmount (e.g. virtualization window slides past) still credits the
      // partial watch toward the threshold.
      if (!fired && startedAt !== 0) {
        watchedMs += performance.now() - startedAt;
        if (watchedMs >= WATCH_THRESHOLD_MS) onWatched(short.id);
      }
      v.removeEventListener('play', onPlay);
      v.removeEventListener('pause', onPauseOrEnd);
      v.removeEventListener('ended', onPauseOrEnd);
    };
  }, [short.id, onWatched]);

  const videoSrc = resolveMediaUrl(short.video.streaming_url || short.video.url);
  const poster = resolveMediaUrl(
    short.video.thumbnail_url || short.main_image?.thumbnail_url || short.main_image?.url,
  );
  const shouldMountSrc = preload !== 'none';

  return (
    <article
      className="short-item"
      ref={(el) => { containerRef.current = el; }}
      data-short-index={index}
      aria-label={short.title}
    >
      {/* Video tap = native play/pause feel; opening PropertyDetails is reserved
          for the explicit "View details" action button. Clicking the media used
          to trigger onSelect, which surprised users who just wanted to mute or
          replay. */}
      <div className="short-item__media" role="presentation">
        {poster && (
          <img
            className="short-item__poster"
            src={poster}
            alt=""
            loading={preload === 'auto' ? 'eager' : 'lazy'}
            decoding="async"
            aria-hidden="true"
          />
        )}
        {shouldMountSrc && videoSrc && (
          <video
            ref={videoRef}
            className="short-item__video"
            src={videoSrc}
            poster={poster}
            preload={preload}
            muted
            loop
            playsInline
            // Most-visible-only: don't fight audio focus; mute is mandatory anyway.
            // eslint-disable-next-line react/no-unknown-property
            disableRemotePlayback
            aria-label={short.title}
          />
        )}
      </div>

      <div className="short-item__overlay">
        <div className="short-item__info">
          <p className="short-item__title">{short.title}</p>
          <p className="short-item__location">{short.location_name}</p>
          <p className="short-item__price">{priceLabel ?? formatPrice(short.price, short.currency, short.listing_type)}</p>
          {short.agent_name && <p className="short-item__agent">by {short.agent_name}</p>}
        </div>
        <div className="short-item__actions">
          <button
            type="button"
            className={`short-item__action short-item__action--like${isFav ? ' is-active' : ''}`}
            onClick={() => toggleFavorite(short.id)}
            aria-label={isFav ? 'Remove from favorites' : 'Add to favorites'}
            aria-pressed={isFav}
          >
            <Icon name={isFav ? 'heartFilled' : 'heart'} size={22} />
            <span className="short-item__tooltip" role="tooltip">
              {isFav ? 'Unfavorite' : 'Favorite'}
            </span>
          </button>
          <button
            type="button"
            className="short-item__action"
            onClick={() => onSelect(short.id)}
            aria-label="View listing details"
          >
            <Icon name="eye" size={22} />
            <span className="short-item__tooltip" role="tooltip">View details</span>
          </button>
          {onDismiss && (
            <button
              type="button"
              className="short-item__action short-item__action--dismiss"
              onClick={() => onDismiss(short.id)}
              aria-label="Hide this listing"
            >
              <Icon name="x" size={22} />
              <span className="short-item__tooltip" role="tooltip">Hide listing</span>
            </button>
          )}
        </div>
      </div>
    </article>
  );
};

// Re-render only when the visible state or data actually changes.
// This is the difference between "60fps scroll" and "jank on every entry".
const MemoShortItemBase = React.memo(ShortItemBase, (prev, next) => (
  prev.short.id === next.short.id &&
  prev.isActive === next.isActive &&
  prev.preload === next.preload &&
  prev.index === next.index &&
  prev.liked === next.liked &&
  prev.priceLabel === next.priceLabel
));

export { MemoShortItemBase as ShortItemBase };

/** Props for the default real-estate item — identical to the original ShortItem surface, so the
 *  real-estate VerticalVideoFeed path is untouched. */
interface ShortItemProps {
  short: PropertyShort;
  index: number;
  isActive: boolean;
  preload: PreloadTier;
  register: (index: number, node: HTMLElement | null) => void;
  onSelect: (id: string) => void;
  onDismiss?: (id: string) => void;
}

/**
 * Default item: wires the like to the shared real-estate favorites store. A thin shell over
 * ShortItemBase — keeps the favorites hooks at a STABLE call site (always mounted for the home
 * feed), so rules-of-hooks hold and the home path renders exactly as before.
 *
 * Membership-only favorite subscription: re-renders only when THIS id's favorite state flips, not
 * on every unrelated favorite toggle elsewhere in the app.
 */
const ShortItem: React.FC<ShortItemProps> = (props) => {
  const isFav = useIsFavorite(props.short.id);
  const toggleFavorite = useToggleFavorite();
  return <MemoShortItemBase {...props} liked={isFav} onToggleLike={toggleFavorite} />;
};

export default ShortItem;
