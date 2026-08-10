// MediaCarousel — the feed card's media surface when a post carries MULTIPLE images / a video.
//
// A swipeable, one-at-a-time carousel (the IG/FB feed pattern): one slide visible, swipe (touch)
// or arrow (desktop/keyboard) to advance, dot indicators show position, video slides play inline.
// Keeps a proximity feed scannable — a tall grid collage of every photo would bury the next post.
//
// Controlled-free: it owns only the current index. A single-media post still renders here (no
// arrows/dots), so the card has ONE media code path. The parent decides whether to mount it at all
// (a text-only post has no media block).
import React, { useRef, useState } from 'react';
import { resolveMediaUrl, isVideoUrl } from '../../utils/media';
import Icon from '../ui/Icon';
import './MediaCarousel.css';

interface MediaCarouselProps {
  /** Raw media URLs (mixed images + an optional video), in publish order. */
  urls: string[];
  /** Alt text / fallback initial source — the post title. */
  title: string;
  /** Tap on a slide (opens the storefront, same as the old single image). */
  onSelect?: () => void;
}

const MediaCarousel: React.FC<MediaCarouselProps> = ({ urls, title, onSelect }) => {
  const [index, setIndex] = useState(0);
  // Touch-swipe tracking — horizontal drag past a threshold advances/retreats one slide.
  const touchStartX = useRef<number | null>(null);

  // The card's media box takes the TRUE aspect ratio of its FIRST slide (measured from the decoded
  // image/video), so a portrait post reads tall and a landscape post reads wide — nothing is cropped
  // (the slides use object-fit: contain). We lock the box to the first slide's ratio (not the
  // current slide's) so swiping a multi-image card never makes the card jump height mid-scroll; a
  // sibling slide of a different orientation simply letterboxes inside the fixed box. null until
  // measured ⇒ the CSS default ratio applies (no layout flash to a wrong shape).
  const [firstRatio, setFirstRatio] = useState<number | null>(null);
  const onFirstImgLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const el = e.currentTarget;
    if (el.naturalWidth > 0 && el.naturalHeight > 0) {
      setFirstRatio(el.naturalWidth / el.naturalHeight);
    }
  };
  const onFirstVideoMeta = (e: React.SyntheticEvent<HTMLVideoElement>) => {
    const el = e.currentTarget;
    if (el.videoWidth > 0 && el.videoHeight > 0) {
      setFirstRatio(el.videoWidth / el.videoHeight);
    }
  };

  const count = urls.length;
  // Clamp defensively (a parent re-render with fewer urls shouldn't strand the index out of range).
  const safeIndex = Math.min(index, Math.max(0, count - 1));
  const current = urls[safeIndex];
  const resolved = resolveMediaUrl(current);
  const isVideo = isVideoUrl(current);

  const go = (next: number) => {
    if (count === 0) return;
    // Wrap around so the arrows are never dead-ends.
    setIndex(((next % count) + count) % count);
  };

  const onTouchStart = (e: React.TouchEvent) => { touchStartX.current = e.touches[0].clientX; };
  const onTouchEnd = (e: React.TouchEvent) => {
    if (touchStartX.current == null) return;
    const dx = e.changedTouches[0].clientX - touchStartX.current;
    touchStartX.current = null;
    if (Math.abs(dx) < 40) return; // ignore taps / tiny drags
    go(safeIndex + (dx < 0 ? 1 : -1));
  };

  if (count === 0) return null;

  // The first slide drives the box ratio (measured once, on load). We render its measuring handler
  // only when that slide is showing so a later slide can't overwrite the locked ratio.
  const isFirstSlide = safeIndex === 0;

  return (
    <div
      className="media-carousel"
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
      data-testid="media-carousel"
      // The true ratio of the first slide (w/h). CSS clamps it to a sane min/max and caps the box
      // height at the available viewport (see MediaCarousel.css). Absent until measured.
      style={firstRatio ? ({ '--media-ratio': String(firstRatio) } as React.CSSProperties) : undefined}
    >
      <div className="media-carousel__stage" role="presentation" onClick={onSelect}>
        {isVideo ? (
          // Inline playback; stop a tap on the controls from also opening the storefront.
          <video
            key={current}
            src={resolved}
            controls
            playsInline
            preload="metadata"
            className="media-carousel__video"
            onClick={(e) => e.stopPropagation()}
            onLoadedMetadata={isFirstSlide ? onFirstVideoMeta : undefined}
            data-testid="carousel-video"
          />
        ) : resolved ? (
          <img
            src={resolved}
            alt={title}
            loading="lazy"
            className="media-carousel__img"
            onLoad={isFirstSlide ? onFirstImgLoad : undefined}
          />
        ) : (
          <span className="media-carousel__fallback" aria-hidden="true">{title.slice(0, 1)}</span>
        )}
      </div>

      {count > 1 && (
        <>
          <button
            type="button"
            className="media-carousel__nav media-carousel__nav--prev"
            onClick={(e) => { e.stopPropagation(); go(safeIndex - 1); }}
            aria-label="Previous media"
            data-testid="carousel-prev"
          >
            <Icon name="chevronLeft" size={20} />
          </button>
          <button
            type="button"
            className="media-carousel__nav media-carousel__nav--next"
            onClick={(e) => { e.stopPropagation(); go(safeIndex + 1); }}
            aria-label="Next media"
            data-testid="carousel-next"
          >
            <Icon name="chevronRight" size={20} />
          </button>

          {/* Count pill (e.g. "2/4") — a quick, honest sense of how much media there is. */}
          <span className="media-carousel__counter" data-testid="carousel-counter">
            {safeIndex + 1}/{count}
          </span>

          <div className="media-carousel__dots" role="tablist" aria-label="Media">
            {urls.map((u, i) => (
              <button
                key={u + i}
                type="button"
                role="tab"
                aria-selected={i === safeIndex}
                aria-label={`Go to media ${i + 1}`}
                className={`media-carousel__dot${i === safeIndex ? ' media-carousel__dot--on' : ''}`}
                onClick={(e) => { e.stopPropagation(); go(i); }}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
};

export default MediaCarousel;
