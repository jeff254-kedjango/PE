// Storefront — the buyer-facing shop page (§8). PAGE-ONLY: mounted by ShopPage under /shop/:key
// (canonical /shop/@<handle> or legacy /shop/<sellerId>). No sheet/overlay mount — the previous
// sheet variant was removed (Chunk A) because it appeared over the navbar and left the /trade
// sidebar visible underneath (double-sidebar). All storefront navigation now goes through the
// /shop/ route: /trade card taps navigate() to /shop/<sellerId>.
//
// §8.4 LOCKED: shops stay a CATALOGUE (name, media, rating, follow) — no likes/comments/shares/
// saves on this surface. Social affordances live on the feed (ProductCard); the storefront is
// where a buyer evaluates a business.
//
// Entry: exactly ONE of `sellerId` or `handle` is required. Both resolve to the SAME
// PublicStorefront DTO via distinct hooks; the other hook is disabled. Once resolved, we fetch
// the shop's profile lazily by shops[0].shop.id so the identity header can render banner +
// category + follower count (fields the storefront DTO doesn't carry). O(1): at most two GETs
// per mount, both cached for 60s.
import React, { useState } from 'react';
import { useShopProfile, useToggleShopFollow } from '../../hooks/useShopProfile';
import { useStorefront, useStorefrontByHandle } from '../../hooks/useStorefront';
import { useSellerReviews } from '../../hooks/useSellerReviews';
import { useShopHeartbeat } from '../../hooks/useShopHeartbeat';
import { resolveMediaUrl } from '../../utils/media';
import { CATEGORY_META, CATEGORY_NEUTRAL_VAR } from '../../utils/categories';
import ShopAvatar from './ShopAvatar';
import {
  formatPrice,
  type CommerceSession, type PublicListing, type PublicStorefront,
  type ReviewOut, type ShopProfile,
} from '../../api/commerce';
import './Storefront.css';

type Tab = 'catalogue' | 'reviews';

// Discriminated input — TypeScript refuses a call that passes both or neither.
type StorefrontKey =
  | { sellerId: string; handle?: never }
  | { sellerId?: never; handle: string };

export interface StorefrontProps {
  session: CommerceSession | null;
  /** Which URL family the caller has — sellerId (legacy /shop/<sellerId>) or handle (canonical
   *  /shop/@<handle>). Exactly one is required. */
  entry: StorefrontKey;
  /** Optional listing-click handler. When set, the catalogue grid becomes clickable and the parent
   *  decides where the click goes (a product detail page, or a modal). When absent, cards are still
   *  rendered but not interactive — the storefront can be used as a pure preview surface. */
  onSelectListing?: (listing: PublicListing) => void;
  /** §8 Chunk C+: the listing the visitor is CURRENTLY viewing (a PDP is open above the storefront).
   *  Null / omitted → the visitor is on the storefront index. The prop threads into the shop
   *  heartbeat body so the seller's Viewing Card can say "Alice is viewing Kikoi tote bag" instead
   *  of just "Alice is here". Latest-wins on the server: clearing this to null on modal close sends
   *  the null in the next 30s ping, which the seller sees as "back to browsing storefront". */
  viewingListingId?: string | null;
}

// ─────────── identity header (banner + avatar + name + category + rating + followers) ───────────
// The header consumes the storefront DTO (display_name + rating + review_count from the SELLER)
// and, when available, the shop profile DTO (banner_url + avatar_url + category + follower_count
// + following). Both are optional at render time so a slow profile fetch doesn't withhold the
// storefront: name and rating render immediately, banner/category/followers fill in when the
// profile lands. The Follow button only mounts once we have a profile + session (the follow
// mutation is a write, and without a session there's nothing to authenticate).
const IdentityHeader: React.FC<{
  storefront: PublicStorefront;
  profile: ShopProfile | undefined;
  canFollow: boolean;
  onToggleFollow: () => void;
  followPending: boolean;
}> = ({ storefront, profile, canFollow, onToggleFollow, followPending }) => {
  const shopName = storefront.shops[0]?.shop.name ?? storefront.display_name;
  const bannerUrl = resolveMediaUrl(profile?.banner_url);
  const avatarUrl = profile?.avatar_url ?? null;
  const category = profile?.category ?? null;
  const meta = category ? CATEGORY_META[category] : undefined;
  const colorVar = meta?.colorVar ?? CATEGORY_NEUTRAL_VAR;
  const followerCount = profile?.follower_count ?? 0;
  const following = profile?.following === true;
  const ratingLabel = storefront.rating != null
    ? `${storefront.rating.toFixed(1)} · ${storefront.review_count} review${storefront.review_count === 1 ? '' : 's'}`
    : 'No ratings yet';

  return (
    <header className="storefront__header" data-testid="storefront-header">
      {/* Banner: seller-published cover; when unset a plain colored band tinted to the category. */}
      <div
        className={`storefront__banner${bannerUrl ? '' : ' storefront__banner--empty'}`}
        style={{ ['--storefront-cat-color' as string]: `var(${colorVar})` }}
      >
        {bannerUrl ? (
          <img className="storefront__banner-img" src={bannerUrl} alt="" aria-hidden="true" />
        ) : null}
      </div>
      <div className="storefront__id">
        <ShopAvatar url={avatarUrl} name={shopName} className="storefront__avatar" />
        <div className="storefront__id-text">
          <h1 className="storefront__name" data-testid="storefront-name">{shopName}</h1>
          <div className="storefront__meta">
            {meta ? (
              <span
                className="storefront__category"
                style={{ ['--storefront-cat-color' as string]: `var(${meta.colorVar})` }}
                data-testid="storefront-category"
              >
                {meta.label}
              </span>
            ) : null}
            <span className="storefront__rating" data-testid="storefront-rating">{ratingLabel}</span>
            <span className="storefront__followers" data-testid="storefront-followers">
              {followerCount} follower{followerCount === 1 ? '' : 's'}
            </span>
          </div>
        </div>
        {/* Follow toggle — only rendered once we have BOTH a shopId-resolved profile AND a session.
            An anonymous viewer sees no button (no dead affordance). The label reads Follow /
            Following (page-level tone); the hovercard uses "Notify" (its own promise verb). */}
        {canFollow && profile ? (
          <button
            type="button"
            className={`storefront__follow${following ? ' storefront__follow--following' : ''}`}
            onClick={onToggleFollow}
            disabled={followPending}
            aria-pressed={following}
            data-testid="storefront-follow"
          >
            {following ? 'Following' : 'Follow'}
          </button>
        ) : null}
      </div>
    </header>
  );
};

// ─────────── catalogue grid ───────────
// One card per PublicListing: image + title + price. Lean by design (§8.4: no likes/saves/comments
// on the storefront body). Cards become buttons ONLY when `onSelect` is supplied — otherwise
// they render as static divs so a preview mount doesn't have unreachable interactive elements.
const CatalogueGrid: React.FC<{
  listings: PublicListing[];
  onSelect?: (listing: PublicListing) => void;
}> = ({ listings, onSelect }) => {
  if (listings.length === 0) {
    return (
      <div className="storefront__empty" data-testid="storefront-catalogue-empty">
        <p>No listings yet.</p>
      </div>
    );
  }
  return (
    <ul className="storefront__grid" data-testid="storefront-catalogue">
      {listings.map((listing) => {
        const cover = resolveMediaUrl(listing.media_urls[0]);
        const priceLabel = formatPrice(listing.price_cents, listing.currency);
        const interactive = !!onSelect;
        return (
          <li key={listing.id} className="storefront__card">
            {interactive ? (
              <button
                type="button"
                className="storefront__card-btn"
                onClick={() => onSelect!(listing)}
                data-testid="storefront-card"
              >
                <CardBody cover={cover} title={listing.title} price={priceLabel} pricing={listing.pricing_mode} />
              </button>
            ) : (
              <div className="storefront__card-btn storefront__card-btn--static" data-testid="storefront-card">
                <CardBody cover={cover} title={listing.title} price={priceLabel} pricing={listing.pricing_mode} />
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
};

/** Shared inner body for the card — extracted so the button/div variants can't drift. */
const CardBody: React.FC<{
  cover: string | undefined;
  title: string;
  price: string;
  pricing: PublicListing['pricing_mode'];
}> = ({ cover, title, price, pricing }) => (
  <>
    <div className="storefront__card-media">
      {cover ? (
        <img src={cover} alt="" aria-hidden="true" loading="lazy" />
      ) : (
        <div className="storefront__card-media--empty" aria-hidden="true" />
      )}
    </div>
    <div className="storefront__card-text">
      <p className="storefront__card-title">{title}</p>
      <p className="storefront__card-price">
        {price}
        {pricing === 'bargain' ? <span className="storefront__card-bargain"> · Bargain</span> : null}
      </p>
    </div>
  </>
);

// ─────────── reviews tab ───────────
// Uses the Chunk-2 useSellerReviews hook (paginated, newest-first). Renders the summary in the
// header (already in IdentityHeader) — this tab only lists rows + a Load-more affordance. An
// unrated seller shows a friendly empty state, NOT an error (the endpoint returns count=0).
const ReviewsTab: React.FC<{
  session: CommerceSession | null;
  sellerId: string;
}> = ({ session, sellerId }) => {
  const q = useSellerReviews(session, sellerId);
  if (q.isLoading) {
    return <div className="storefront__reviews-loading" data-testid="storefront-reviews-loading">Loading reviews…</div>;
  }
  if (q.isError) {
    return <div className="storefront__empty" data-testid="storefront-reviews-error"><p>Couldn't load reviews.</p></div>;
  }
  if (q.items.length === 0) {
    return <div className="storefront__empty" data-testid="storefront-reviews-empty"><p>No reviews yet.</p></div>;
  }
  return (
    <div className="storefront__reviews" data-testid="storefront-reviews">
      <ul className="storefront__review-list">
        {q.items.map((r) => <ReviewRow key={r.id} review={r} />)}
      </ul>
      {q.hasNextPage ? (
        <button
          type="button"
          className="storefront__load-more"
          disabled={q.isFetchingNextPage}
          onClick={() => q.fetchNextPage()}
          data-testid="storefront-reviews-more"
        >
          {q.isFetchingNextPage ? 'Loading…' : 'Load more'}
        </button>
      ) : null}
    </div>
  );
};

const ReviewRow: React.FC<{ review: ReviewOut }> = ({ review }) => {
  // Star row: filled to the review's rating (1..5) with muted trailing stars. Screen-readers get
  // the numeric via aria-label; the visual stars are aria-hidden.
  const stars = '★★★★★'.slice(0, review.rating) + '☆☆☆☆☆'.slice(0, 5 - review.rating);
  const date = new Date(review.created_at).toLocaleDateString();
  return (
    <li className="storefront__review" data-testid="storefront-review">
      <div className="storefront__review-head">
        <span className="storefront__review-stars" aria-label={`${review.rating} out of 5`}>{stars}</span>
        <span className="storefront__review-date">{date}</span>
      </div>
      {review.body ? <p className="storefront__review-body">{review.body}</p> : null}
    </li>
  );
};

const Storefront: React.FC<StorefrontProps> = ({ session, entry, onSelectListing, viewingListingId = null }) => {
  // Resolve the storefront via the right lookup path. Exactly one hook is enabled — the other's
  // `enabled` gate keeps it silent, so no wasted request.
  const bySellerId = useStorefront(session, entry.sellerId ?? null);
  const byHandle = useStorefrontByHandle(session, entry.handle ?? null);
  const query = entry.sellerId != null ? bySellerId : byHandle;

  // Lazy shop profile (banner + category + followers + `following`) — fires ONLY after the
  // storefront resolves, keyed by the shop id (shops[0]). Empty shops list ⇒ no profile fetch;
  // identity header degrades to storefront-only data.
  const shopId = query.data?.shops[0]?.shop.id ?? null;
  const profile = useShopProfile(session, shopId ?? '', !!shopId);
  // Follow mutation binds to the SAME shopId. When shopId is null the hook is idle; we still
  // instantiate it unconditionally to keep the hook-order rule (React refuses conditional hooks).
  // Its mutation only runs when we call .mutate(), gated by `canFollow` in the button.
  const toggleFollow = useToggleShopFollow(session, shopId ?? '');
  const canFollow = !!session && !!shopId;
  const onToggleFollow = () => {
    if (!canFollow || toggleFollow.isPending) return;
    toggleFollow.mutate();
  };

  // §8 Chunk C: ping the shop's heartbeat every 30s while this page is mounted so the seller's
  // Viewing Card sees a live visitor. The hook is idle until (session, shopId) are both set;
  // for anonymous browsers, session is null and the storefront query would have failed to load
  // anyway — no wasted heartbeats.
  useShopHeartbeat({
    session,
    shopId,
    commerceUrl: session?.commerce_url ?? null,
    // §8 Chunk C+: forward the currently-open listing so the seller sees "viewing X" per row.
    viewingListingId,
  });

  // Tab state — Catalogue by default; buyer flips to Reviews. Deliberately in-component state, not
  // URL-linked: a review-tab deep-link is a small enhancement, not core to the storefront.
  const [tab, setTab] = useState<Tab>('catalogue');

  if (query.isLoading) {
    return (
      <section className="storefront" aria-busy="true" data-testid="storefront-loading">
        <div className="storefront__skeleton">
          <div className="storefront__skeleton-banner" />
          <div className="storefront__skeleton-id">
            <div className="storefront__skeleton-avatar" />
            <div className="storefront__skeleton-lines">
              <div className="storefront__skeleton-line storefront__skeleton-line--name" />
              <div className="storefront__skeleton-line" />
            </div>
          </div>
        </div>
      </section>
    );
  }

  if (query.isError || !query.data) {
    return (
      <section className="storefront" data-testid="storefront-error">
        <div className="storefront__empty">
          <p>We couldn't load this shop.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="storefront" data-testid="storefront">
      <IdentityHeader
        storefront={query.data}
        profile={profile.data}
        canFollow={canFollow}
        onToggleFollow={onToggleFollow}
        followPending={toggleFollow.isPending}
      />
      {/* Tab strip — Catalogue / Reviews. role=tablist for screen-readers; the panels below
          declare their tab via aria-labelledby so the association survives when the buyer
          navigates by keyboard. Only two tabs today (§8.4: shops are a catalogue), so no arrow
          navigation — plain click/tap is enough. */}
      <div className="storefront__tabs" role="tablist" aria-label="Storefront sections">
        <button
          type="button"
          role="tab"
          id="storefront-tab-catalogue"
          aria-selected={tab === 'catalogue'}
          aria-controls="storefront-panel-catalogue"
          className={`storefront__tab${tab === 'catalogue' ? ' storefront__tab--active' : ''}`}
          onClick={() => setTab('catalogue')}
          data-testid="storefront-tab-catalogue"
        >
          Catalogue
        </button>
        <button
          type="button"
          role="tab"
          id="storefront-tab-reviews"
          aria-selected={tab === 'reviews'}
          aria-controls="storefront-panel-reviews"
          className={`storefront__tab${tab === 'reviews' ? ' storefront__tab--active' : ''}`}
          onClick={() => setTab('reviews')}
          data-testid="storefront-tab-reviews"
        >
          Reviews
        </button>
      </div>
      {tab === 'catalogue' ? (
        <div
          role="tabpanel"
          id="storefront-panel-catalogue"
          aria-labelledby="storefront-tab-catalogue"
          className="storefront__panel"
        >
          <CatalogueGrid
            listings={query.data.shops.flatMap((s) => s.listings)}
            onSelect={onSelectListing}
          />
        </div>
      ) : (
        <div
          role="tabpanel"
          id="storefront-panel-reviews"
          aria-labelledby="storefront-tab-reviews"
          className="storefront__panel"
        >
          <ReviewsTab session={session} sellerId={query.data.seller_id} />
        </div>
      )}
    </section>
  );
};

export default Storefront;
