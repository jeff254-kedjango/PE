/* ==========================================================================
   IMAGE GALLERY / LIGHTBOX
   Fullscreen overlay with Stories-style progress bar.
   Mobile: swipeable. Desktop: arrow keys + click navigation.
   Lazy-loaded thumbnails strip at the bottom.
   ========================================================================== */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { PropertyImage } from '../../types/propertyApi';
import { resolveMediaUrl } from '../../utils/media';
import Icon from './Icon';
import './ImageGallery.css';

interface ImageGalleryProps {
  images: PropertyImage[];
  initialIndex?: number;
  onClose: () => void;
}

const SWIPE_THRESHOLD = 50;

const ImageGallery: React.FC<ImageGalleryProps> = ({ images, initialIndex = 0, onClose }) => {
  const [current, setCurrent] = useState(initialIndex);
  const touchStartX = useRef(0);
  const touchDeltaX = useRef(0);
  const trackRef = useRef<HTMLDivElement>(null);
  const count = images.length;

  const goTo = useCallback((i: number) => {
    setCurrent(Math.max(0, Math.min(count - 1, i)));
  }, [count]);

  const prev = useCallback(() => goTo(current - 1), [current, goTo]);
  const next = useCallback(() => goTo(current + 1), [current, goTo]);

  // Keyboard navigation
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'ArrowLeft') prev();
      if (e.key === 'ArrowRight') next();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose, prev, next]);

  // Lock body scroll
  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, []);

  // Touch handlers for swipe
  const onTouchStart = useCallback((e: React.TouchEvent) => {
    touchStartX.current = e.touches[0].clientX;
    touchDeltaX.current = 0;
  }, []);

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    touchDeltaX.current = e.touches[0].clientX - touchStartX.current;
  }, []);

  const onTouchEnd = useCallback(() => {
    if (touchDeltaX.current > SWIPE_THRESHOLD) prev();
    else if (touchDeltaX.current < -SWIPE_THRESHOLD) next();
    touchDeltaX.current = 0;
  }, [prev, next]);

  return (
    <div className="ig-overlay" role="dialog" aria-label="Image gallery">
      {/* Backdrop — click to close */}
      <div className="ig-backdrop" onClick={onClose} />

      {/* Close button */}
      <button className="ig-close" onClick={onClose} aria-label="Close gallery">
        <Icon name="x" size={20} />
      </button>

      {/* Counter */}
      <span className="ig-counter">{current + 1} / {count}</span>

      {/* Stories progress bar */}
      {count > 1 && (
        <div className="ig-progress">
          {images.map((_, i) => (
            <button
              key={i}
              className={`ig-progress__bar ${i === current ? 'ig-progress__bar--active' : i < current ? 'ig-progress__bar--done' : ''}`}
              onClick={() => goTo(i)}
              aria-label={`Go to image ${i + 1}`}
            />
          ))}
        </div>
      )}

      {/* Main image track */}
      <div
        className="ig-track-wrapper"
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
      >
        <div
          ref={trackRef}
          className="ig-track"
          style={{ transform: `translateX(-${current * 100}%)` }}
        >
          {images.map((img, i) => (
            <div key={img.id} className="ig-slide">
              <img
                src={resolveMediaUrl(img.url || img.thumbnail_url)}
                alt={img.alt_text ?? `Image ${i + 1}`}
                loading={Math.abs(i - current) <= 1 ? 'eager' : 'lazy'}
                draggable={false}
              />
            </div>
          ))}
        </div>
      </div>

      {/* Arrow buttons (desktop) */}
      {current > 0 && (
        <button className="ig-arrow ig-arrow--prev" onClick={prev} aria-label="Previous image">
          <Icon name="chevronLeft" size={24} />
        </button>
      )}
      {current < count - 1 && (
        <button className="ig-arrow ig-arrow--next" onClick={next} aria-label="Next image">
          <Icon name="chevronRight" size={24} />
        </button>
      )}

      {/* Thumbnail strip */}
      {count > 1 && (
        <div className="ig-thumbs">
          {images.map((img, i) => (
            <button
              key={img.id}
              className={`ig-thumb ${i === current ? 'ig-thumb--active' : ''}`}
              onClick={() => goTo(i)}
              aria-label={`View image ${i + 1}`}
            >
              <img
                src={resolveMediaUrl(img.thumbnail_url || img.url)}
                alt={img.alt_text ?? `Thumbnail ${i + 1}`}
                loading="lazy"
                draggable={false}
              />
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

export default ImageGallery;
