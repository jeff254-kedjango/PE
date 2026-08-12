// ViewingCard — the LEFT-column "who's on my shop right now" card (§8, Chunk C; C+ hydration).
//
// Shape:
//   ┌ Viewing (3) ─────────────────────────────────────────┐
//   │  [Live] [History]                                    │  ← toggle
//   │                                                      │
//   │  ┌───┐  Alice · Kilimani                             │
//   │  │IMG│  viewing Kikoi tote bag                       │
//   │  └───┘                                                │
//   │  ┌───┐  Guest · CBD                                  │
//   │  │ · │  browsing storefront                          │
//   │  └───┘                                                │
//   │                                                       │
//   │  [ Promote my shop ]                                  │
//   └───────────────────────────────────────────────────────┘
//
// Live tab (§8 Chunk C+): rows of hydrated viewers — avatar, display name, area label,
// product being viewed (or "browsing storefront" for storefront-index visits). The count
// appears in small type next to the "Viewing" title, driven by the SAME payload as the
// row list so counter + list can never disagree.
//
// History tab: infinite-scroll list of past visits with a calendar date-range filter.
//
// Promote button: boosts every active in-stock listing for 2h (a shop-wide evergreen boost).
import React, { useState } from 'react';
import {
  useShopLiveViewers,
  useShopViewHistory,
  usePromoteAllShop,
} from '../../../hooks/useShopViewers';
import type { CommerceSession, LiveViewerOut, ShopOut } from '../../../api/commerce';
import { resolveMediaUrl } from '../../../utils/media';
import './ViewingCard.css';

type Tab = 'live' | 'history';

interface ViewingCardProps {
  session: CommerceSession | null;
  /** The shop this card is scoped to. When `null`, the card renders an empty state prompting
   *  the seller to open a shop first (mirrors RankingCard's `no_shop` branch). */
  shop: ShopOut | null;
}

/** Default promote duration — 2 hours (7200s), inside the server's 5min..24h band. */
const PROMOTE_DURATION_SECONDS = 7200;

/** Fallback single-letter avatar when the viewer has no avatar_url (anonymous, missing
 *  weespas record, or hidden-avatar user). Deliberately monogram not emoji — a face-shaped
 *  emoji would misgender/miscategorize the actual person. */
function avatarInitial(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return '·';
  const first = Array.from(trimmed)[0];
  return first ? first.toUpperCase() : '·';
}

/** The viewer's profile picture, or their monogram.
 *
 *  Three states, because a remote image has three outcomes and each needs its own treatment:
 *
 *    1. **No URL** (anonymous viewer, bridge unavailable, or a weespas user who never uploaded)
 *       → monogram immediately. Not a failure, just an absence.
 *    2. **URL present, still loading** → the monogram sits under a shimmer. A viewer row is
 *       ~40px of avatar next to text; an unstyled empty circle popping into a face is the
 *       jarring layout flash `loading="lazy"` otherwise buys us.
 *    3. **URL present, load FAILED** (404, offline media dir, hotlink block) → fall back to the
 *       monogram permanently. This is the case that was broken: without an `onError` the row
 *       kept an empty hole where a face should be, and the seller had no way to tell a viewer
 *       with no picture apart from one whose picture didn't load.
 *
 *  `resolveMediaUrl` is the reason this exists at all. weespas stores avatars RELATIVE
 *  (`/uploads/avatars/x.webp`, see weespas/routers/me.py) and commerce passes the value
 *  straight through, so a raw `src` resolves against the Vite dev origin (:5174) instead of
 *  the backend (:8000) — there is no dev proxy — and every real avatar 404s. Same helper +
 *  same error-fallback contract as `ShopAvatar`, so the two surfaces can't drift. */
const ViewerAvatar: React.FC<{ v: LiveViewerOut }> = ({ v }) => {
  const [broken, setBroken] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const resolved = resolveMediaUrl(v.avatar_url);
  const initial = avatarInitial(v.display_name);

  if (!resolved || broken) {
    return (
      <span
        className="viewing-card__viewer-avatar viewing-card__viewer-avatar--fallback"
        aria-hidden
        data-testid="viewing-card-viewer-initial"
      >
        {initial}
      </span>
    );
  }

  // The monogram stays mounted UNDER the image until it loads, so the row never shows an
  // empty circle: shimmer + initial first, real face second. Once loaded the img is opaque
  // and covers it. Both are aria-hidden — the adjacent name is the accessible identity.
  return (
    <span
      className={
        loaded
          ? 'viewing-card__viewer-avatar-wrap'
          : 'viewing-card__viewer-avatar-wrap viewing-card__viewer-avatar-wrap--loading skeleton'
      }
      aria-hidden
    >
      {!loaded && <span className="viewing-card__viewer-avatar-ghost">{initial}</span>}
      <img
        className={
          loaded
            ? 'viewing-card__viewer-avatar'
            : 'viewing-card__viewer-avatar viewing-card__viewer-avatar--pending'
        }
        src={resolved}
        alt=""              /* decorative — the name is already shown below */
        loading="lazy"
        decoding="async"
        onLoad={() => setLoaded(true)}
        onError={() => setBroken(true)}
        data-testid="viewing-card-viewer-avatar"
      />
    </span>
  );
};

const ViewerRow: React.FC<{ v: LiveViewerOut }> = ({ v }) => {
  const areaBits: string[] = [];
  if (v.area_label) areaBits.push(v.area_label);
  const activityLine = v.viewing_listing_title
    ? `viewing ${v.viewing_listing_title}`
    : 'browsing storefront';
  return (
    <li className="viewing-card__viewer" data-testid="viewing-card-viewer-row">
      <ViewerAvatar v={v} />
      <div className="viewing-card__viewer-meta">
        <span className="viewing-card__viewer-name">
          {v.display_name}
          {areaBits.length > 0 && (
            <>
              <span className="viewing-card__viewer-sep" aria-hidden> · </span>
              <span className="viewing-card__viewer-area">{areaBits.join(', ')}</span>
            </>
          )}
        </span>
        <span className="viewing-card__viewer-activity">{activityLine}</span>
        {v.phone && (
          <a className="viewing-card__viewer-phone" href={`tel:${v.phone}`}>
            {v.phone}
          </a>
        )}
      </div>
    </li>
  );
};

const ViewingCard: React.FC<ViewingCardProps> = ({ session, shop }) => {
  const [tab, setTab] = useState<Tab>('live');
  const [since, setSince] = useState<string>('');
  const [until, setUntil] = useState<string>('');
  const [promoteFeedback, setPromoteFeedback] = useState<string | null>(null);

  const shopId = shop?.id ?? null;

  const live = useShopLiveViewers(session, shopId);
  const sinceIso = since ? new Date(`${since}T00:00:00Z`).toISOString() : null;
  const untilIso = until ? new Date(`${until}T23:59:59.999Z`).toISOString() : null;
  const history = useShopViewHistory(session, shopId, { since: sinceIso, until: untilIso });
  const promote = usePromoteAllShop(session);

  const onPromote = () => {
    if (!shopId || promote.isPending) return;
    setPromoteFeedback(null);
    promote.mutate(
      { shopId, durationSeconds: PROMOTE_DURATION_SECONDS },
      {
        onSuccess: (r) => setPromoteFeedback(
          r.promoted_count > 0
            ? `Boosted ${r.promoted_count} listing${r.promoted_count === 1 ? '' : 's'} for 2 hours.`
            : 'No active in-stock listings to promote.',
        ),
        onError: (e) => setPromoteFeedback(`Couldn’t promote: ${e.message}`),
      },
    );
  };

  // The (N) counter next to the header comes from the live payload; while loading we show
  // nothing (no misleading "(0)"). The counter binds to the LIVE tab's data even when the
  // History tab is active — the seller still wants to know at a glance who's on the shop.
  const liveCount = live.data?.count;

  return (
    <section className="viewing-card" aria-labelledby="viewing-card-title">
      <header className="viewing-card__head">
        <h2 id="viewing-card-title" className="viewing-card__title">
          Viewing
          {typeof liveCount === 'number' && (
            <span className="viewing-card__title-count" aria-label={`${liveCount} live`}>
              {' '}({liveCount})
            </span>
          )}
        </h2>
        <div className="viewing-card__toggle" role="tablist" aria-label="Live or history">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'live'}
            className={`viewing-card__tab${tab === 'live' ? ' is-active' : ''}`}
            onClick={() => setTab('live')}
          >
            Live
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'history'}
            className={`viewing-card__tab${tab === 'history' ? ' is-active' : ''}`}
            onClick={() => setTab('history')}
          >
            History
          </button>
        </div>
      </header>

      <div className="viewing-card__body">
        {!shop && (
          <p className="viewing-card__state" role="status">Open a shop to see who’s viewing.</p>
        )}

        {shop && tab === 'live' && (
          <div className="viewing-card__live">
            {live.isLoading && <p className="viewing-card__state" role="status">Counting…</p>}
            {live.isError && (
              <p className="viewing-card__state viewing-card__state--error" role="alert">
                Couldn’t load live viewers. {live.error?.message ?? ''}
              </p>
            )}
            {live.data && live.data.items.length === 0 && (
              <p className="viewing-card__state viewing-card__state--empty" role="status">
                No one’s viewing right now.
              </p>
            )}
            {live.data && live.data.items.length > 0 && (
              <ul className="viewing-card__viewers" aria-label="Live viewers">
                {live.data.items.map((v) => (
                  <ViewerRow key={v.session_id} v={v} />
                ))}
              </ul>
            )}
          </div>
        )}

        {shop && tab === 'history' && (
          <div className="viewing-card__history">
            <div className="viewing-card__filters">
              <label className="viewing-card__filter-label">
                From
                <input
                  type="date"
                  value={since}
                  onChange={(e) => setSince(e.target.value)}
                  aria-label="View history start date"
                />
              </label>
              <label className="viewing-card__filter-label">
                To
                <input
                  type="date"
                  value={until}
                  onChange={(e) => setUntil(e.target.value)}
                  aria-label="View history end date"
                />
              </label>
            </div>
            {history.isLoading && <p className="viewing-card__state" role="status">Loading history…</p>}
            {history.isError && (
              <p className="viewing-card__state viewing-card__state--error" role="alert">
                Couldn’t load history. {history.error?.message ?? ''}
              </p>
            )}
            {history.data && (
              <>
                <ul className="viewing-card__history-list">
                  {history.data.pages.flatMap((p) => p.items).map((row) => (
                    <li key={row.id} className="viewing-card__history-item">
                      <span className="viewing-card__history-who">
                        {row.viewer_uuid ? 'Registered visitor' : 'Guest'}
                      </span>
                      <span className="viewing-card__history-when">
                        {new Date(row.viewed_at).toLocaleString()}
                      </span>
                    </li>
                  ))}
                  {history.data.pages.every((p) => p.items.length === 0) && (
                    <li className="viewing-card__history-empty">No visits in this range.</li>
                  )}
                </ul>
                {history.hasNextPage && (
                  <button
                    type="button"
                    className="viewing-card__more"
                    onClick={() => history.fetchNextPage()}
                    disabled={history.isFetchingNextPage}
                  >
                    {history.isFetchingNextPage ? 'Loading…' : 'Load more'}
                  </button>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {shop && (
        <div className="viewing-card__promote">
          <button
            type="button"
            className="viewing-card__promote-btn"
            onClick={onPromote}
            disabled={promote.isPending}
            aria-busy={promote.isPending}
          >
            {promote.isPending ? 'Promoting…' : 'Promote my shop'}
          </button>
          {promoteFeedback && (
            <p className="viewing-card__promote-feedback" role="status">{promoteFeedback}</p>
          )}
        </div>
      )}
    </section>
  );
};

export default ViewingCard;
