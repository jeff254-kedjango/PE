// ProductCard — one post in the SOCIAL proximity feed (a sale IS a post, architecture §8).
//
// This is the feed's social-media surface (FB/X/LinkedIn hybrid): a large media-led post with a
// seller header and an engagement bar (Save · Share · Ask · Comments). The SHOP/storefront stays a
// traditional catalogue (Storefront) — only the feed is social. Target users are
// Millennials/GenZ; depth of interaction is the moat, no follower graph required (§8).
//
// Honesty contracts rendered here (non-negotiable, mirror the backend) — UNCHANGED from the
// catalogue card:
//   * "Boosted" label whenever is_sponsored — paid reach, NOT a higher organic rank (§8.3). Never
//     suppressed.
//   * "Selling now" pill when is_promoted (and not sponsored) — a live §8 ephemeral window.
//   * ConfirmedShield = ground-verified PROVENANCE, NOT a safety verdict (shared component).
//   * A "Video" badge marks a seller's declared short-video post (is_short_video) so a buyer knows
//     what they're opening — the §8 "guide users on what is what" requirement.
// Distance is shown plainly (a national-boosted item can be far — we don't hide that).
import React, { useMemo, useState } from 'react';
import Icon from '../ui/Icon';
import ConfirmedShield from '../ui/ConfirmedShield';
import ShopAvatar from './ShopAvatar';
import CommentThread from './CommentThread';
import MediaCarousel from './MediaCarousel';
import ShopHoverCard from './ShopHoverCard';
import { useToggleSave, useCreateInquiry } from '../../hooks/useEngagement';
import useHeartPop from '../../hooks/useHeartPop';
import { isVideoUrl } from '../../utils/media';
import {
  formatPrice, formatDistance, previewText, needsTruncation,
  type FeedItem, type CommerceSession,
} from '../../api/commerce';
import './ProductCard.css';

interface ProductCardProps {
  item: FeedItem;
  /** True when this listing's building has a recorded on-the-ground assessment (batch-resolved
   *  by the feed via useConfirmedListings). */
  confirmed: boolean;
  /** Commerce bridge session — powers the engagement bar (save / ask / comments). When null the
   *  bar renders read-only counts (the page gates on auth before mounting the feed, so in practice
   *  this is always present; the guard keeps the card pure/testable). */
  session?: CommerceSession | null;
  /** Open the seller's storefront (the post header / "view shop" affordance). */
  onSelect: (item: FeedItem) => void;
}

const StarRating: React.FC<{ rating: number; count: number }> = ({ rating, count }) => (
  <span className="product-card__rating" title={`${rating.toFixed(1)} from ${count} review${count === 1 ? '' : 's'}`}>
    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 2l2.9 6.3 6.9.7-5.1 4.6 1.4 6.8L12 17.8 5.9 20.4l1.4-6.8L2.2 9l6.9-.7z" />
    </svg>
    <span>{rating.toFixed(1)}</span>
    <span className="product-card__rating-count">({count})</span>
  </span>
);

/** Render free text into paragraphs, preserving the seller's line breaks. A blank line starts a
 *  new <p>; single newlines within a block become <br>. Plain text only (React escapes it) — no
 *  HTML is interpreted, so this is XSS-safe. */
const Paragraphs: React.FC<{ text: string }> = ({ text }) => {
  const blocks = text.split(/\n{2,}/).map((b) => b.trim()).filter(Boolean);
  return (
    <>
      {blocks.map((block, i) => (
        <p key={i} className="product-card__desc-p">
          {block.split('\n').map((line, j) => (
            <React.Fragment key={j}>
              {j > 0 && <br />}
              {line}
            </React.Fragment>
          ))}
        </p>
      ))}
    </>
  );
};

const ProductCard: React.FC<ProductCardProps> = ({ item, confirmed, session, onSelect }) => {
  // The Listings timeline is images-only (§8): declared short-video posts live in the Videos
  // overlay, not here. Drop any video slide from the carousel so a mixed image+video post shows
  // its images only. The "Video" badge is still driven by is_short_video (below) so a buyer knows
  // the post has a clip to open in Videos.
  const imageUrls = useMemo(
    () => item.media_urls.filter((u) => !isVideoUrl(u)),
    [item.media_urls],
  );
  const hasMedia = imageUrls.length > 0;

  // Engagement-bar local state. save_count AND the caller's own saved-state come from the feed item
  // (server truth at fetch, resolved per page with no N+1); we seed both from the item so the heart
  // reflects prior saves on mount, then track the toggle result locally.
  const [saved, setSaved] = useState(item.saved_by_me);
  const [saveCount, setSaveCount] = useState(item.save_count);
  const [commentsOpen, setCommentsOpen] = useState(false);
  const [asked, setAsked] = useState(false);
  const [shared, setShared] = useState(false);
  const [descExpanded, setDescExpanded] = useState(false);

  // A plain POST (§8 timeline) has no price/stock and its text IS the body — the card suppresses
  // price + the "Ask" inquiry action and shows the body in full (no "read more" — it's the post,
  // not a product blurb). A product keeps the title/price head + the truncated description.
  const isPost = item.post_kind === 'post';
  const description = (item.description ?? '').trim();
  const descTruncatable = !isPost && needsTruncation(description);

  const toggleSave = useToggleSave(session ?? null, item.id);
  const createInquiry = useCreateInquiry(session ?? null, item.id);

  const { popping, pop } = useHeartPop();
  const handleSave = () => {
    if (!session || toggleSave.isPending) return;
    pop(); // tap feedback fires on the gesture, independent of the request's outcome
    toggleSave.mutate(undefined, {
      onSuccess: (r) => { setSaved(r.saved); setSaveCount(r.save_count); },
    });
  };

  const handleAsk = () => {
    if (!session || createInquiry.isPending || asked) return;
    // The canonical "is this available?" inquiry — a private message to the seller's inbox.
    createInquiry.mutate(undefined, { onSuccess: () => setAsked(true) });
  };

  const handleShare = async () => {
    const url = `${window.location.origin}/trade/sellers/${encodeURIComponent(item.seller_id)}`;
    try {
      if (navigator.share) {
        await navigator.share({ title: item.title, url });
      } else if (navigator.clipboard) {
        await navigator.clipboard.writeText(url);
        setShared(true);
      }
    } catch { /* user cancelled — no-op */ }
  };

  return (
    <article
      className={`product-card${item.is_sponsored ? ' product-card--sponsored' : ''}`}
      data-testid="product-card"
    >
      {/* ── Post header: seller identity + open-storefront affordance. The AVATAR opens the shop
             profile hovercard (hover on desktop, tap on mobile); the META row opens the storefront.
             They're separate controls so neither nests a button inside a button. ── */}
      <header className="product-card__header">
        <div className="product-card__seller">
          <ShopHoverCard
            session={session ?? null}
            shopId={item.shop_id}
            onOpenProfile={() => onSelect(item)}
          >
            <ShopAvatar
              url={item.shop_avatar_url}
              name={item.shop_name ?? item.title}
              className="product-card__avatar"
            />
          </ShopHoverCard>
          <button
            type="button"
            className="product-card__seller-meta product-card__seller-meta--btn"
            onClick={() => onSelect(item)}
            data-testid="open-storefront"
          >
            {item.shop_name && (
              <span className="product-card__shop-name">{item.shop_name}</span>
            )}
            <span className="product-card__seller-line">
              <span className="product-card__distance">{formatDistance(item.distance_m)}</span>
              {confirmed && <ConfirmedShield size={14} />}
            </span>
            {item.seller_rating != null && (
              <StarRating rating={item.seller_rating} count={item.seller_review_count} />
            )}
          </button>
        </div>
        {item.is_sponsored && (
          <span className="product-card__boosted" data-testid="boosted-label">
            Boosted{item.boost_tier ? ` · ${item.boost_tier}` : ''}
          </span>
        )}
        {item.is_promoted && !item.is_sponsored && (
          <span className="product-card__selling-now" data-testid="selling-now">Selling now</span>
        )}
      </header>

      {/* ── Media (large, social). MULTIPLE images / a video render in a swipeable carousel
             (one slide at a time, arrows + dots). A text-only POST has none — skip the block
             entirely rather than show a fallback letter; a product with no media still shows a
             fallback initial so the card isn't headless. The "Video" badge marks a declared
             short-video post regardless. ── */}
      {hasMedia ? (
        <div className="product-card__media-wrap">
          <MediaCarousel urls={imageUrls} title={item.title} onSelect={() => onSelect(item)} />
          {item.is_short_video && (
            <span className="product-card__video-badge" data-testid="video-badge">
              <Icon name="play" size={12} /> Video
            </span>
          )}
        </div>
      ) : (
        !isPost && (
          <div className="product-card__media" onClick={() => onSelect(item)} role="presentation">
            <span className="product-card__media-fallback" aria-hidden="true">{item.title.slice(0, 1)}</span>
          </div>
        )
      )}

      {/* ── Caption: a product shows title + price + truncated description; a post shows its body in full ── */}
      <div className="product-card__caption">
        {!isPost && (
          <div className="product-card__caption-head">
            <h3 title={item.title}>{item.title}</h3>
            <div className="product-card__price">{formatPrice(item.price_cents, item.currency)}</div>
          </div>
        )}

        {isPost && description && (
          <div className="product-card__post-body" data-testid="post-body">
            <Paragraphs text={description} />
          </div>
        )}

        {!isPost && description && (
          <div className="product-card__desc" data-testid="product-desc">
            {descTruncatable && !descExpanded ? (
              <p className="product-card__desc-p">
                {previewText(description)}
                {'… '}
                <button
                  type="button"
                  className="product-card__readmore"
                  onClick={() => setDescExpanded(true)}
                  data-testid="read-more"
                >
                  read more
                </button>
              </p>
            ) : (
              <>
                <Paragraphs text={description} />
                {descTruncatable && (
                  <button
                    type="button"
                    className="product-card__readmore"
                    onClick={() => setDescExpanded(false)}
                    data-testid="read-less"
                  >
                    show less
                  </button>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {/* ── Engagement bar: Save · Share · Ask · Comments ── */}
      <div className="product-card__engage" role="group" aria-label="Post actions">
        <button
          type="button"
          className={`product-card__action product-card__action--like${saved ? ' product-card__action--on' : ''}`}
          onClick={handleSave}
          disabled={!session || toggleSave.isPending}
          aria-pressed={saved}
          data-testid="save-btn"
        >
          <Icon name={saved ? 'heartFilled' : 'heart'} size={18} className={popping ? 'animate-heart' : ''} />
          <span>{saveCount > 0 ? saveCount : 'Save'}</span>
        </button>

        <button
          type="button"
          className="product-card__action"
          onClick={handleShare}
          data-testid="share-btn"
        >
          <Icon name="share" size={18} />
          <span>{shared ? 'Copied' : 'Share'}</span>
        </button>

        {/* "Ask" is a product inquiry — a plain post has no seller to ask about a price/stock. */}
        {!isPost && (
          <button
            type="button"
            className={`product-card__action product-card__action--ask${asked ? ' product-card__action--on' : ''}`}
            onClick={handleAsk}
            disabled={!session || createInquiry.isPending || asked}
            data-testid="ask-btn"
          >
            <Icon name="mail" size={18} />
            <span>{asked ? 'Asked' : 'Ask'}</span>
          </button>
        )}

        <button
          type="button"
          className={`product-card__action${commentsOpen ? ' product-card__action--on' : ''}`}
          onClick={() => setCommentsOpen((o) => !o)}
          aria-expanded={commentsOpen}
          data-testid="comments-btn"
        >
          <Icon name="chat" size={18} />
          <span>{item.comment_count > 0 ? item.comment_count : 'Comment'}</span>
        </button>
      </div>

      {/* Comment thread mounts (and fetches) ONLY when opened — no N+1 across the feed. */}
      {commentsOpen && <CommentThread session={session ?? null} listingId={item.id} />}
    </article>
  );
};

export default ProductCard;
