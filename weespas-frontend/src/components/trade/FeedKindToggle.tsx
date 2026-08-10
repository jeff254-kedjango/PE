// FeedKindToggle — the §8 "Shops | Clips | Podcasts" lane switch on the social Trade feed.
//
// Renders as the BOTTOM EDGE of the composer card: the composer sits directly above it with a 2px
// gap, and this bar spans the composer's exact width (both are capped at the same 560px stack), so
// the two read as one control surface rather than two floating widgets.
//
// LANE vs WIRE TYPE — the load-bearing distinction:
//   `TradeLane` (here) is a pure UI concept with THREE lanes. `FeedKind` (api/commerce.ts) is the
//   WIRE type and has only two values, because the commerce backend validates `?kind=` against
//   FEED_KINDS = ('listings','videos') and returns 422 for anything else. Keeping them as separate
//   types is what makes it impossible to send 'podcasts' to the API — the compiler rejects it.
//   `laneToFeedKind` is the single, explicit crossing point between the two.
import React from 'react';
import Icon from '../ui/Icon';
import type { FeedKind } from '../../api/commerce';
import './FeedKindToggle.css';

/** The three UI lanes. NOT the wire type — see `laneToFeedKind`. */
export type TradeLane = 'shops' | 'clips' | 'podcasts';

/** Lane → backend `?kind=`. `null` ⇒ this lane has NO backend feed to query yet.
 *
 *  'shops'    → the existing listings feed (a relabel: same data, seller-facing wording).
 *  'clips'    → the existing short-video feed (a relabel of "Videos").
 *  'podcasts' → null. There is no audio anywhere in the stack (no model, and the upload MIME
 *               allowlist in weespas/routers/media.py is images+video only), so this lane renders
 *               an honest "coming soon" panel instead of querying an endpoint that would 422.
 */
export const laneToFeedKind = (lane: TradeLane): FeedKind | null =>
  lane === 'shops' ? 'listings' : lane === 'clips' ? 'videos' : null;

interface LaneSpec {
  lane: TradeLane;
  label: string;
  icon: 'store' | 'video' | 'podcast';
}

// Order is the visual order. Declared once so the markup below can't drift from the type.
const LANES: readonly LaneSpec[] = [
  { lane: 'shops', label: 'Shops', icon: 'store' },
  { lane: 'clips', label: 'Clips', icon: 'video' },
  { lane: 'podcasts', label: 'Podcasts', icon: 'podcast' },
] as const;

interface FeedKindToggleProps {
  /** The active lane. (The page defaults to 'shops'.) */
  lane: TradeLane;
  onChange: (lane: TradeLane) => void;
}

const FeedKindToggle: React.FC<FeedKindToggleProps> = ({ lane, onChange }) => (
  <div className="feed-kind-toggle" role="tablist" aria-label="Feed type">
    {LANES.map((spec) => {
      const active = lane === spec.lane;
      return (
        <button
          key={spec.lane}
          type="button"
          role="tab"
          aria-selected={active}
          className={`feed-kind-toggle__btn${active ? ' feed-kind-toggle__btn--active' : ''}`}
          onClick={() => onChange(spec.lane)}
          data-testid={`kind-${spec.lane}`}
        >
          <Icon name={spec.icon} size={16} />
          <span>{spec.label}</span>
        </button>
      );
    })}
  </div>
);

export default FeedKindToggle;
