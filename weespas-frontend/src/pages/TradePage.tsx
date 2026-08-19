// TradePage — the buyer's "what's selling near me" surface (commerce FE-1).
//
// Gates on auth (commerce needs a token), mints the commerce session via the bridge, resolves the
// buyer's location (with an honest default + a prompt when denied — we never fabricate a position
// silently), and hosts the proximity feed + the tap-to-open public storefront panel.
import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useGeolocation } from '../hooks/useGeolocation';
import { useCommerceSession } from '../hooks/useCommerceSession';
import { useCommerceVideoShorts } from '../hooks/useCommerceVideoShorts';
import ProductFeed from '../components/trade/ProductFeed';
import ComposerBox from '../components/trade/ComposerBox';
import FeedKindToggle, { type TradeLane } from '../components/trade/FeedKindToggle';
// Chunk A: TradePage no longer mounts <Storefront> as an overlay. Tapping a seller navigates to
// /shop/<sellerId>, which ShopPage resolves and canonicalizes to /shop/@<handle> when present.
import TrendingRail from '../components/trade/TrendingRail';
import MarketsSection from '../components/trade/MarketsSection';
import ShopVideoStrip from '../components/trade/ShopVideoStrip';
import VerticalVideoFeed from '../components/shorts/VerticalVideoFeed';
import QuickBuys from '../components/trade/QuickBuys';
import FlashSales from '../components/trade/FlashSales';
import PageMeta from '../components/ui/PageMeta';
import { widenNoteText } from '../components/trade/widenNote';
import './TradePage.css';

// Default centre when the buyer hasn't granted location yet. The feed still renders (so the page
// isn't empty), and a banner invites enabling precise location. This is an HONEST default (clearly
// labelled "approximate"), not a silent wrong-position.
//
// DEV NOTE: this points at the centroid of our demo-shop coverage (Kilimani/Kileleshwa/South C —
// the AOIs also covered by InSAR, so commerce features can be exercised against real building
// data). It was previously Nairobi CBD (-1.2921, 36.8219), but the demo pool was relocated west of
// there, so a CBD default landed on the sparse-widen fallback. Revisit for prod: the honest
// production default is the user's city centre, not a demo cluster.
const DEFAULT_LAT = -1.2907;
const DEFAULT_LNG = 36.7895;

const TradePage: React.FC = () => {
  const { isAuthenticated, token, user } = useAuth();
  const navigate = useNavigate();

  const { session, isLoading: sessionLoading, error: sessionError } = useCommerceSession();
  const { latitude, longitude, error: geoError, requestLocation } = useGeolocation();

  // Chunk A: no more overlay-mounted storefront. Any legacy /trade/sellers/:sellerId deep-link
  // is caught at the top of the component and redirects to /shop/:sellerId (ShopPage does the
  // rest — including canonicalizing to /shop/@<handle> when the shop has one).
  const { sellerId: legacySellerId } = useParams<{ sellerId?: string }>();
  useEffect(() => {
    if (legacySellerId) navigate(`/shop/${encodeURIComponent(legacySellerId)}`, { replace: true });
  }, [legacySellerId, navigate]);

  // §8 Shops | Clips | Podcasts lane toggle, seated under the composer.
  //   'shops'    → the social image timeline (ProductFeed, images-only cards).
  //   'clips'    → the shared full-screen TikTok-style vertical player opens over the page.
  //   'podcasts' → no backend exists (no audio anywhere in the stack); renders an honest panel.
  // Default 'shops' (the everyday feed).
  //
  // This is the UI LANE, not the wire kind — FeedKind ('listings'|'videos') is what the commerce API
  // accepts, and 'podcasts' deliberately has no wire value so it can never reach the API. The only
  // crossing point is laneToFeedKind (FeedKindToggle.tsx).
  const [lane, setLane] = useState<TradeLane>('shops');
  // Which short the vertical overlay jumps to on open (null ⇒ start at the top). Mirrors App.tsx's
  // videoFeedInitialId: tapping a specific rail tile opens the overlay AT that clip.
  const [videoInitialId, setVideoInitialId] = useState<string | null>(null);

  // Ask for precise location once on mount; if denied we fall back to the default centre.
  useEffect(() => { requestLocation(); }, [requestLocation]);

  // Bottom-anchored sticky right column: the `top` inset that pins its BOTTOM 10px above the
  // viewport bottom is `100vh − <live column height> − 10px` (see TradePage.css for why `top`, not
  // `bottom`). CSS can't read the column's pixel height, so measure it with a ResizeObserver and
  // publish it into the `--rail-right-h` custom property the rule reads. Runs only where the rail
  // exists (≥1101px it's laid out; <1101px it's display:none and the sticky rule doesn't apply, so
  // a stale value is harmless). No-ops gracefully if ResizeObserver is unavailable.
  const railRightRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const el = railRightRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const publish = () => el.style.setProperty('--rail-right-h', `${Math.round(el.offsetHeight)}px`);
    publish();
    const ro = new ResizeObserver(publish);
    ro.observe(el);
    return () => ro.disconnect();
  }, [session, lane]);

  const hasPreciseLocation = latitude != null && longitude != null;
  const lat = hasPreciseLocation ? latitude : DEFAULT_LAT;
  const lng = hasPreciseLocation ? longitude : DEFAULT_LNG;

  // The Videos surface — one shared fetch + save-state, feeding BOTH the right-rail shelf and the
  // full-screen overlay (opened by the toggle or a rail tile). React Query dedupes the request.
  const video = useCommerceVideoShorts({ session, lat, lng });

  // Honest auto-widen note for the Videos lane: when the buyer's immediate radius held fewer than a
  // page of shorts the backend widened once to pull in the nearest content (video.widened) and we
  // tell them how far it is — distance ONLY, never a delivery claim (see widenNote.ts).
  // video.immediateCount splits the copy so it never claims "nothing nearby" when a few local shorts
  // are shown. The SAME string feeds the rail shelf and the full-screen overlay so the two never
  // drift. null ⇒ nothing honest to say (no banner).
  const videoWidenNote = widenNoteText(video.widened, video.nearestDistanceM, video.immediateCount);

  // Chunk A: navigate to the storefront page. ShopPage resolves the sellerId, and if the shop
  // has a claimed handle it replaces the URL to /shop/@<handle> (frontend-only canonical
  // redirect). There's no in-page state to open/close anymore — the browser back button is
  // the return path.
  const openStorefront = (sellerId: string) => {
    navigate(`/shop/${encodeURIComponent(sellerId)}`);
  };

  if (!isAuthenticated) {
    return (
      <div className="trade-page trade-page--gate">
        <PageMeta title="Trade" description="Discover what neighbours are selling near you on Weespas." />
        <h1>Trade</h1>
        <p>Sign in to see what’s selling around you.</p>
        <Link to="/login?next=trade" className="trade-page__cta">Sign in</Link>
      </div>
    );
  }

  return (
    <div className="trade-page">
      <PageMeta title="Trade" description="Discover what neighbours are selling near you on Weespas." />

      {/* Flow row: [sticky trending rail][feed column][remaining space]. The rail is the FIRST child
          of the page (NOT below a header), so its natural top sits flush under the navbar — only a
          deliberate margin separates them (see .trade-page padding-top + the rail's sticky top). It
          is position:sticky so it pins below the navbar and scrolls away when the footer enters
          view. The page header + toggle live INSIDE the feed column so they don't push the rail
          down. Below 1100px the rail hides itself and this row collapses to the centred feed. */}
      {session && (
        <div className="trade-page__layout">
          <TrendingRail session={session} lat={lat} lng={lng} onSelectSeller={openStorefront} />

          <div className="trade-page__feed-col">
            {/* Composer FIRST, lane toggle seated directly beneath it as the same card (2px seam,
                matched width — see TradePage.css + FeedKindToggle.css). Posting is the primary act on
                this column, so it leads; the lane toggle filters what appears BELOW it, which is why
                it belongs between the composer and the feed rather than above both. */}
            <ComposerBox
              session={session}
              weespasToken={token}
              lat={lat}
              lng={lng}
              authorName={user?.name ?? null}
            />

            <div className="trade-page__toggle-row">
              {/* Clips opens the full-screen vertical overlay from the top; Shops closes it and
                  returns to the image timeline. Flipping to Clips starts at the top (initialId null). */}
              <FeedKindToggle
                lane={lane}
                onChange={(next) => {
                  if (next === 'clips') setVideoInitialId(null);
                  setLane(next);
                }}
              />
            </div>

            {/* §WeesStock F4: the investor market, inline on the trade page — responsive
                placement. On MOBILE/TABLET the section lives here in the feed column (the right
                rail is hidden <1101px, so a rail-only mount would vanish); on DESKTOP (≥1101px)
                this instance is CSS-hidden and the rail instance below takes over, matching the
                three-column design [trending | feed | WeesStock Markets]. The 2×3 tile grid is
                the glance surface; its header button opens the full /markets board (which
                carries the regulatory label — this section links there rather than restating it
                at tile size). */}
            <MarketsSection session={session} />

            {sessionError && (
              <p className="trade-page__state trade-page__state--error" role="alert">
                Couldn’t start a trade session. {sessionError.message}
              </p>
            )}

            {/* Podcasts has no backend: there is no audio model in commerce and weespas's upload
                allowlist is images+video only. Say that plainly rather than render an empty feed that
                looks like "no podcasts near you" — the absence is ours, not the neighbourhood's. */}
            {lane === 'podcasts' && (
              <p className="trade-page__state" data-testid="lane-podcasts-empty">
                Podcasts aren’t live yet — we’re still building this lane.
              </p>
            )}

            {/* The image timeline is ALWAYS the listings feed — Clips is the overlay, not a feed
                mode here, so the timeline stays put underneath it. Hidden (not unmounted) on the
                Podcasts lane so its fetch + scroll position survive a lane round-trip. */}
            <div hidden={lane === 'podcasts'}>
              <ProductFeed
                session={session}
                lat={lat}
                lng={lng}
                // A hard geolocation error with no coords yet means we're on the default centre; we
                // don't block the feed (default still renders), but surface the prompt via the header.
                locationDenied={false}
                onRequestLocation={requestLocation}
                onSelectSeller={openStorefront}
              />
            </div>

            {/* geoError is informational — the feed already degrades to the default centre. The retry
                lives HERE inside the always-visible feed column (not only the right rail, which is
                display:none <1101px), so a buyer who denied geo on mobile/tablet can still re-request
                precise location. Tapping it re-prompts; the feed stays rendered either way. */}
            {geoError && hasPreciseLocation === false && (
              <p className="trade-page__geo-hint">
                Location unavailable — showing Nairobi’s centre.{' '}
                <button type="button" className="trade-page__geo-retry" onClick={requestLocation}>
                  Search my location
                </button>
              </p>
            )}
          </div>

          {/* Right column: location + Sell controls (matched size) and the nearby short-video strip.
              "Search my location" always shows — it re-prompts / re-centres and is harmless once precise
              location is granted. Hidden <1100px with the rest of this column (TradePage.css). */}
          <aside className="trade-page__rail-right" aria-label="Trade controls" ref={railRightRef}>
            {/* §WeesStock F4 — the DESKTOP instance of the market grid: the design's third column
                [what's trending | feed | WeesStock Markets]. Hidden <1101px with the rail itself;
                the feed-column instance covers those widths (see MarketsSection.css). The same
                component mounted twice shares one React Query cache, so this adds no extra
                fetch. */}
            <MarketsSection session={session} />
            <button type="button" className="trade-page__rail-btn trade-page__rail-btn--ghost" onClick={requestLocation}>
              Search my location
            </button>
            <Link to="/trade/sell" className="trade-page__rail-btn trade-page__rail-btn--solid">
              Sell
            </Link>
            {/* Honest widen note above the rail shelf: the nearby shorts came from beyond the
                immediate radius, so we say how far — distance only (never delivery). */}
            {videoWidenNote && (
              <p className="trade-page__rail-note" role="status">{videoWidenNote}</p>
            )}
            {/* The rail shelf shares the page's video shorts + price labeller; tapping a tile opens
                the same overlay AT that clip (sets lane='clips' + the initial id). */}
            <ShopVideoStrip
              shorts={video.shorts}
              priceLabelFor={video.priceLabelFor}
              onOpenVideo={(id) => { setVideoInitialId(id); setLane('clips'); }}
              // Chunk 1 (permanent columns): the strip stays MOUNTED even with zero shorts, so the
              // right column keeps its width on load. Shimmer while the initial fetch is in flight,
              // then swap to the honest "no clips nearby" placeholder.
              isLoading={video.isLoading}
            />
            {/* §8 Quick Buys — a 3×3 paged near/interest product grid with its own price/category/
                radius filter. Sits below the video strip; inherits the rail's <1101px hide. */}
            <QuickBuys session={session} lat={lat} lng={lng} onSelectSeller={openStorefront} />
            {/* §8 Flash Sales — the nationwide "crazy offer" grid (3×2, ranked by craziness). Sits
                directly under Quick Buys; inherits the rail's <1101px hide. */}
            <FlashSales session={session} lat={lat} lng={lng} onSelectSeller={openStorefront} />
          </aside>
        </div>
      )}

      {/* Before the commerce session resolves there's no rail/feed yet — keep the page from being
          blank with a minimal connecting state (header lives in the feed column once session lands). */}
      {!session && sessionLoading && <p className="trade-page__state">Connecting…</p>}
      {!session && sessionError && (
        <p className="trade-page__state trade-page__state--error" role="alert">
          Couldn’t start a trade session. {sessionError.message}
        </p>
      )}

      {/* Chunk A: storefront overlay removed. openStorefront navigates to /shop/<sellerId>,
          which lives on its own route (ShopPage). No more in-page sheet — that variant broke the
          layout (rendered above the navbar, and left the /trade sidebar visible underneath). */}

      {/* §8 Clips — the shared full-screen vertical player (the SAME reused component the real-estate
          landing page opens). CONTROLLED by our video shorts + save state. Rendered in a portal on
          document.body so it escapes the page's max-width/stacking context and truly fills the
          viewport. VerticalVideoFeed owns its own empty/loading/error states (empty ⇒ "Back to
          listings" → onExit), so we mount it whenever the lane is 'clips'. */}
      {lane === 'clips' && createPortal(
        <VerticalVideoFeed
          token={null}
          items={video.shorts}
          initialShortId={videoInitialId}
          onExit={() => { setLane('shops'); setVideoInitialId(null); }}
          onSelect={(id) => { const s = video.sellerById.get(id); if (s) openStorefront(s); }}
          isLiked={video.isLiked}
          onToggleLike={video.toggleLike}
          priceLabelFor={video.priceLabelFor}
          // Same honest widen banner over the full-screen player as the rail shows (single source).
          notice={videoWidenNote}
          // Commerce has its own analytics surface; no real-estate property-view recording here.
          onWatched={() => {}}
        />,
        document.body,
      )}
    </div>
  );
};

export default TradePage;
