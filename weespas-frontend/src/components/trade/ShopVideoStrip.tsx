// ShopVideoStrip — the §8 Trade right-rail short-video shelf, built by REUSING the weespas shorts UI.
//
// This is a THIN presentational orchestrator: it feeds pre-adapted commerce shorts into the exact
// 3-up horizontal poster row the real-estate shorts use (<ShortsShelf>), with the card chrome
// suppressed (`hideHeader` + the bare `.shop-video-strip` variant — "remove the card"). Tapping a
// tile asks the PAGE to open the shared full-screen vertical player (the same overlay the middle-
// column Videos toggle opens), so the strip and the toggle are one experience with one save state.
//
// The commerce→shorts adaptation and SAVE state now live in useCommerceVideoShorts (shared with the
// TradePage overlay) — this component only renders.
//
// Chunk 1 (permanent columns): when there are no shorts nearby the strip STAYS MOUNTED with an
// honest placeholder + shimmering skeleton tiles, so the right column keeps its width on load
// instead of collapsing and reflowing the page. We do NOT invoke <ShortsShelf> in the empty state
// — that shelf's own empty copy is aimed at real-estate ("No videos in this area") and its chrome
// includes a chevron nav row that would look broken with no tiles behind it.
import React from 'react';
import ShortsShelf from '../shorts/ShortsShelf';
import Icon from '../ui/Icon';
import type { PropertyShort } from '../../api/shorts';
import './ShopVideoStrip.css';

interface ShopVideoStripProps {
  /** Pre-adapted commerce video shorts (from useCommerceVideoShorts). */
  shorts: PropertyShort[];
  /** KES price label for a short (shared with the vertical overlay). */
  priceLabelFor: (short: PropertyShort) => string;
  /** Open the shared full-screen vertical player at this short (the page owns the overlay). */
  onOpenVideo: (id: string) => void;
  /** Whether the commerce-shorts fetch is still in flight. When true the empty state shimmers;
   *  when false it shows a static "no clips nearby" placeholder. `undefined` treated as false. */
  isLoading?: boolean;
}

const ShopVideoStrip: React.FC<ShopVideoStripProps> = ({ shorts, priceLabelFor, onOpenVideo, isLoading = false }) => {
  // Empty ⇒ mounted placeholder (skeleton while loading, honest text after). The wrapper keeps the
  // strip's outer chrome consistent so column height doesn't jump when data lands.
  if (shorts.length === 0) {
    return (
      <section
        className="shop-video-strip shop-video-strip--empty"
        aria-label="Nearby shop videos"
        data-testid="shop-video-strip-empty"
      >
        <div className="shop-video-strip__tiles" aria-hidden="true">
          {/* Two skeleton tiles matching the shelf's --shorts-visible-count:2. Class `.skeleton`
              comes from styles/animations.css (global reduced-motion guard in reset.css). */}
          <div className="skeleton shop-video-strip__tile-skeleton" />
          <div className="skeleton shop-video-strip__tile-skeleton" />
        </div>
        {!isLoading && (
          <div className="shop-video-strip__placeholder" data-testid="shop-video-strip-placeholder">
            <Icon name="play" size={18} className="shop-video-strip__placeholder-icon" />
            <span className="shop-video-strip__placeholder-title">No clips nearby yet</span>
            <span className="shop-video-strip__placeholder-sub">
              Shop videos in your area will show up here.
            </span>
          </div>
        )}
      </section>
    );
  }

  return (
    <ShortsShelf
      shorts={shorts}
      isLoading={false}
      isError={false}
      // Tapping a tile (or "see all") opens the page's full-screen vertical feed at that video.
      onSelect={(id) => onOpenVideo(id)}
      onSeeAll={() => onOpenVideo(shorts[0].id)}
      hideHeader
      className="shop-video-strip"
      // Commerce has no rent/sale badge; price uses KES formatting (no /mo).
      showListingBadge={false}
      priceLabelFor={priceLabelFor}
      // Commerce clips carry no generated thumbnail — show the video's first frame as the still.
      posterFromVideo
    />
  );
};

export default ShopVideoStrip;
