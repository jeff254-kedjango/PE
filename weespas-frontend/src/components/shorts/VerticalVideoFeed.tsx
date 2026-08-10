// src/components/shorts/VerticalVideoFeed.tsx
import React, { useCallback, useEffect, useMemo, useRef } from 'react';
import ShortItem, { ShortItemBase, type PreloadTier } from './ShortItem';
import Icon from '../ui/Icon';
import { useShortsFeed } from '../../hooks/useShortsFeed';
import { useActiveIndex } from '../../hooks/useVideoAutoplay';
import type { PropertyShort } from '../../api/shorts';
import './VerticalVideoFeed.css';

interface VerticalVideoFeedProps {
  token: string | null;
  onSelect: (id: string) => void;
  onDismiss?: (id: string) => void;
  isDismissed?: (id: string) => boolean;
  onExit?: () => void;
  /** If provided, the feed opens with this short scrolled into view and playing. */
  initialShortId?: string | null;
  /**
   * Render as an in-page sticky column instead of a fixed full-screen overlay.
   * Drops the body scroll-lock and the global keydown handler (the page owns
   * scrolling), and switches the root to the `--embedded` CSS variant.
   */
  embedded?: boolean;
  /**
   * CONTROLLED mode. When supplied, the feed renders THESE items instead of fetching the
   * real-estate property-shorts feed (and disables internal pagination — the caller owns the data).
   * Used by the commerce Trade strip, which adapts FeedItem→PropertyShort. When omitted, the feed
   * behaves exactly as before (real-estate path).
   */
  items?: PropertyShort[];
  /** Controlled-mode like state for an id (e.g. commerce save). Default path uses favorites store. */
  isLiked?: (id: string) => boolean;
  /** Controlled-mode like toggle for an id. Default path uses favorites store. */
  onToggleLike?: (id: string) => void;
  /** Controlled-mode per-item price label. Default path uses real-estate formatPrice. */
  priceLabelFor?: (short: PropertyShort) => string;
  /** Controlled-mode watch callback. Default path records real-estate analytics. */
  onWatched?: (id: string) => void;
  /** Optional honest banner shown over the POPULATED feed (e.g. commerce's "closest shops are
   *  within X km" when the immediate radius was empty and the feed widened). null/undefined ⇒ no
   *  banner. Purely informational — it never gates playback. */
  notice?: string | null;
}

/**
 * Full-screen vertical short-video feed (TikTok-style).
 *
 * Performance levers — these matter at scroll time:
 *   1. ONE shared IntersectionObserver in `useActiveIndex` (not per-item).
 *   2. Preload tiering: only the active and ±1 neighbours mount their <video>
 *      src; all others render the poster only (preload="none"). Cuts decoder
 *      pressure to two concurrent streams max — the rule of thumb on mobile.
 *   3. Virtualization window of ±2 around the active index — far items are
 *      rendered as zero-cost spacer divs so the scroll height stays correct.
 *   4. React.memo on ShortItem so neighbours don't re-render when activeIndex
 *      walks past them.
 *   5. Auto-fetch the next page when the user is within 3 of the end.
 */
const VerticalVideoFeed: React.FC<VerticalVideoFeedProps> = ({
  token,
  onSelect,
  onDismiss,
  isDismissed,
  onExit,
  initialShortId,
  embedded = false,
  items: controlledItems,
  isLiked,
  onToggleLike,
  priceLabelFor,
  onWatched,
  notice,
}) => {
  const controlled = controlledItems !== undefined;
  // In controlled mode the caller owns the data — disable the real-estate fetch (the hook must
  // still be called for rules-of-hooks, but `enabled:false` means it never requests).
  const feed = useShortsFeed(token, undefined, !controlled);
  const items = controlled ? controlledItems! : feed.items;
  const isLoading = controlled ? false : feed.isLoading;
  const isError = controlled ? false : feed.isError;
  const { fetchNextPage, hasNextPage, isFetchingNextPage } = feed;

  const visible = useMemo<PropertyShort[]>(
    () => (isDismissed ? items.filter((s) => !isDismissed(s.id)) : items),
    [items, isDismissed],
  );

  const { register, activeIndex } = useActiveIndex(visible.length);
  const lastPrefetchRef = useRef(-1);
  const trackRef = useRef<HTMLDivElement | null>(null);

  // Scroll the TRACK ONLY — never the page. `Element.scrollIntoView()` walks up
  // and scrolls every scrollable ancestor (including the window), which in
  // embedded mode dragged the whole agents page along with the nav buttons (the
  // tablet bug). Scrolling the track by the target's offset relative to the
  // track keeps the movement contained to this component and is identical in
  // full-screen mode (where the body is scroll-locked anyway). O(1) per click.
  const scrollToIndex = useCallback((idx: number) => {
    if (idx < 0 || idx >= visible.length) return;
    const track = trackRef.current;
    if (!track) return;
    const target = track.querySelector<HTMLElement>(`[data-short-index="${idx}"]`);
    if (!target) return;
    const delta = target.getBoundingClientRect().top - track.getBoundingClientRect().top;
    track.scrollBy({ top: delta, behavior: 'smooth' });
  }, [visible.length]);

  // Jump to `initialShortId` once on open. We use scrollIntoView (not state) so
  // the IntersectionObserver inside useActiveIndex picks it up naturally — that
  // keeps active-index logic single-sourced and avoids a competing manual setter.
  // Honored only once per mount; subsequent prop changes (rare) re-trigger only
  // when the id actually changes.
  const appliedInitialIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!initialShortId) return;
    if (appliedInitialIdRef.current === initialShortId) return;
    const idx = visible.findIndex((s) => s.id === initialShortId);
    if (idx < 0) return; // id not in current window — try again when list grows.
    const track = trackRef.current;
    if (!track) return;
    const target = track.querySelector<HTMLElement>(`[data-short-index="${idx}"]`);
    if (!target) return;
    appliedInitialIdRef.current = initialShortId;
    // `instant` avoids a long smooth-scroll animation from the top while the
    // user expects the chosen video to be there immediately.
    target.scrollIntoView({ behavior: 'auto', block: 'start' });
  }, [initialShortId, visible]);

  // Stable per-index ref callbacks so spacers don't unregister/re-register on
  // every parent re-render (activeIndex updates re-render this component).
  const spacerRefsRef = useRef<Map<number, (el: HTMLDivElement | null) => void>>(new Map());
  const getSpacerRef = useCallback((idx: number) => {
    const cache = spacerRefsRef.current;
    let cb = cache.get(idx);
    if (!cb) {
      cb = (el: HTMLDivElement | null) => register(idx, el);
      cache.set(idx, cb);
    }
    return cb;
  }, [register]);

  // Drop cached spacer callbacks when the visible list shrinks below their index.
  useEffect(() => {
    const cache = spacerRefsRef.current;
    cache.forEach((_, idx) => {
      if (idx >= visible.length) cache.delete(idx);
    });
  }, [visible.length]);

  // Pull the next page early so the user doesn't see an empty viewport at the bottom. Skipped in
  // controlled mode — the caller owns the data and there's no internal query to page.
  useEffect(() => {
    if (controlled) return;
    if (!hasNextPage || isFetchingNextPage) return;
    if (activeIndex >= visible.length - 3 && activeIndex !== lastPrefetchRef.current) {
      lastPrefetchRef.current = activeIndex;
      fetchNextPage();
    }
  }, [controlled, activeIndex, visible.length, hasNextPage, isFetchingNextPage, fetchNextPage]);

  // Keyboard navigation: ESC closes, Arrow/j-k jump to prev/next short.
  // Using scrollIntoView keeps the IntersectionObserver as the single source
  // of truth for `activeIndex` — we don't manually set state.
  //
  // When PropertyDetails is layered above the feed (user clicked "View
  // Details"), its panel takes keyboard focus: ArrowUp/Down should scroll
  // the details body, not the feed underneath. We detect that by looking
  // for a mounted `.pd-panel` — cheaper than threading a context just for
  // this and keeps the feed self-contained.
  // Embedded mode lets the page own scrolling: a window-level handler that
  // preventDefault()s Arrow/j-k would hijack page scroll, and the on-screen
  // nav buttons + native scroll-snap already drive the column. So skip it.
  useEffect(() => {
    if (embedded) return;
    const handler = (e: KeyboardEvent) => {
      // Don't hijack typing in inputs/contenteditable.
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      // PropertyDetails (or any open dialog) wins keyboard focus.
      if (document.querySelector('.pd-panel')) return;
      if (e.key === 'Escape' && onExit) { onExit(); return; }
      if (e.key === 'ArrowDown' || e.key === 'j') {
        e.preventDefault();
        scrollToIndex(activeIndex + 1);
      } else if (e.key === 'ArrowUp' || e.key === 'k') {
        e.preventDefault();
        scrollToIndex(activeIndex - 1);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [embedded, onExit, activeIndex, scrollToIndex]);

  // Body scroll lock while the feed is mounted — only in full-screen mode.
  // Embedded, the agents page must keep scrolling.
  useEffect(() => {
    if (embedded) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, [embedded]);

  // Applied to every return branch — the loading/error/empty `--state` cards
  // are `position:fixed; inset:0` too, so they'd go full-screen if not embedded.
  const rootClass = `vertical-video-feed${embedded ? ' vertical-video-feed--embedded' : ''}`;

  const tierFor = (idx: number): PreloadTier => {
    const delta = Math.abs(idx - activeIndex);
    if (delta === 0) return 'auto';
    if (delta === 1) return 'metadata';
    return 'none';
  };

  const renderWindow = 2; // ±2 around active are real items; rest are spacers.

  if (isLoading && visible.length === 0) {
    return (
      <div className={`${rootClass} vertical-video-feed--state`}>
        <div className="vertical-video-feed__spinner" aria-label="Loading short videos" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className={`${rootClass} vertical-video-feed--state`}>
        <p className="vertical-video-feed__message">Couldn't load videos. Try again later.</p>
        {onExit && (
          <button type="button" className="vertical-video-feed__exit-btn" onClick={onExit}>
            Back to listings
          </button>
        )}
      </div>
    );
  }

  if (visible.length === 0) {
    return (
      <div className={`${rootClass} vertical-video-feed--state`}>
        <p className="vertical-video-feed__message">No video listings yet — check back soon.</p>
        {onExit && (
          <button type="button" className="vertical-video-feed__exit-btn" onClick={onExit}>
            Back to listings
          </button>
        )}
      </div>
    );
  }

  return (
    <div className={rootClass}>
      {onExit && (
        <button
          type="button"
          className="vertical-video-feed__close"
          onClick={onExit}
          aria-label="Close video feed"
        >
          <Icon name="x" size={22} />
        </button>
      )}
      {/* Honest auto-widen banner: shown when the caller widened past the immediate radius to find
          the nearest shorts. Informational only — playback is unaffected. Distance-only (no
          delivery claim). */}
      {notice && (
        <p className="vertical-video-feed__notice" role="status">{notice}</p>
      )}
      {/* Desktop-only step controls — hidden on touch via CSS. Mobile users use
          swipe + the native scroll-snap track instead. */}
      <div className="vertical-video-feed__nav" aria-hidden={visible.length <= 1}>
        <button
          type="button"
          className="vertical-video-feed__nav-btn"
          onClick={() => scrollToIndex(activeIndex - 1)}
          disabled={activeIndex <= 0}
          aria-label="Previous video"
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <polyline points="18 15 12 9 6 15" />
          </svg>
        </button>
        <button
          type="button"
          className="vertical-video-feed__nav-btn"
          onClick={() => scrollToIndex(activeIndex + 1)}
          disabled={activeIndex >= visible.length - 1}
          aria-label="Next video"
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      </div>
      <div className="vertical-video-feed__track" ref={trackRef}>
        {visible.map((short, idx) => {
          const inWindow = Math.abs(idx - activeIndex) <= renderWindow;
          if (!inWindow) {
            return (
              <div
                key={short.id}
                className="vertical-video-feed__spacer"
                data-short-index={idx}
                ref={getSpacerRef(idx)}
                aria-hidden="true"
              />
            );
          }
          // Controlled (commerce) → ShortItemBase with injected like-state/price/watch. Uncontrolled
          // (real-estate) → default ShortItem, which wires the shared favorites store internally.
          if (controlled) {
            return (
              <ShortItemBase
                key={short.id}
                short={short}
                index={idx}
                isActive={idx === activeIndex}
                preload={tierFor(idx)}
                register={register}
                onSelect={onSelect}
                onDismiss={onDismiss}
                liked={isLiked ? isLiked(short.id) : false}
                onToggleLike={onToggleLike ?? (() => {})}
                priceLabel={priceLabelFor?.(short)}
                onWatched={onWatched}
              />
            );
          }
          return (
            <ShortItem
              key={short.id}
              short={short}
              index={idx}
              isActive={idx === activeIndex}
              preload={tierFor(idx)}
              register={register}
              onSelect={onSelect}
              onDismiss={onDismiss}
            />
          );
        })}
      </div>
    </div>
  );
};

export default VerticalVideoFeed;
