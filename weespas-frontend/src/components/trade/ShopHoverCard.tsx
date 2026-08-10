// ShopHoverCard — the §8 shop profile popover over a post's seller avatar.
//
// Cross-device trigger: opens on HOVER on desktop (pointer: fine) and on TAP on touch devices
// (pointer: coarse), since a pure :hover is a dead-end on touch. Dismisses on mouse-leave (desktop)
// / outside-tap / Esc. It is a lightweight hovercard, NOT a full-screen modal (the spec left the
// final shape to UI judgement — a hovercard keeps the buyer in the feed).
//
// Content (all seller-published / opaque — no PII, S6): the shop name, an optional contact line, an
// optional description (the backend already caps it at ≤200 words), the follower count, and two
// actions:
//   * "Notify" — toggle a follow/subscription (Follow ⇄ Following).
//   * "Store Front" — open the seller's public storefront (delegated to the parent).
// The profile is fetched LAZILY (only while the card is open) via useShopProfile — no N+1 across
// the feed.
import React, { useEffect, useId, useRef, useState } from 'react';
import { useShopProfile, useToggleShopFollow } from '../../hooks/useShopProfile';
import Icon from '../ui/Icon';
import ShopAvatar from './ShopAvatar';
import { resolveMediaUrl } from '../../utils/media';
import type { CommerceSession } from '../../api/commerce';
import './ShopHoverCard.css';

interface ShopHoverCardProps {
  session: CommerceSession | null;
  shopId: string;
  /** The avatar/trigger to wrap (the seller initial bubble). */
  children: React.ReactNode;
  /** Open the seller's public storefront (the "Profile" button + a fallback). */
  onOpenProfile: () => void;
}

// Coarse pointer ⇒ touch device: we trigger on tap, not hover. Evaluated once at mount (a device
// doesn't switch pointer classes mid-session in practice) and guarded for non-browser test envs.
function isCoarsePointer(): boolean {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(pointer: coarse)').matches;
}

const ShopHoverCard: React.FC<ShopHoverCardProps> = ({ session, shopId, children, onOpenProfile }) => {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<number | null>(null);
  const coarse = useRef(isCoarsePointer());
  const panelId = useId();

  const { data, isLoading, isError } = useShopProfile(session, shopId, open);
  const toggleFollow = useToggleShopFollow(session, shopId);

  const cancelClose = () => {
    if (closeTimer.current) { window.clearTimeout(closeTimer.current); closeTimer.current = null; }
  };
  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = window.setTimeout(() => setOpen(false), 140);
  };
  useEffect(() => cancelClose, []);

  // Outside-tap + Esc dismiss (the primary path on touch, a backstop on desktop).
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // Desktop: hover opens/closes (with a small close delay so moving into the panel doesn't dismiss).
  const hoverProps = coarse.current
    ? {}
    : { onMouseEnter: () => { cancelClose(); setOpen(true); }, onMouseLeave: scheduleClose };

  // Touch: the trigger toggles the card.
  const onTriggerClick = () => { if (coarse.current) setOpen((o) => !o); };

  const handleFollow = () => {
    if (!session || toggleFollow.isPending) return;
    toggleFollow.mutate();
  };

  const handleProfile = () => { setOpen(false); onOpenProfile(); };

  return (
    <div className="shop-hovercard" ref={wrapRef} {...hoverProps}>
      <button
        type="button"
        className="shop-hovercard__trigger"
        onClick={onTriggerClick}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        data-testid="shop-avatar-trigger"
      >
        {children}
      </button>

      {open && (
        <div
          id={panelId}
          className="shop-hovercard__panel"
          role="dialog"
          aria-label="Shop profile"
          data-testid="shop-hovercard"
        >
          {isLoading && <p className="shop-hovercard__state">Loading…</p>}
          {isError && <p className="shop-hovercard__state" role="alert">Couldn’t load this shop.</p>}
          {data && (
            <>
              {/* Cover + avatar as one overlapping unit (classic profile layout): the shop's cover
                  picture is the backdrop, and the avatar sits ON TOP of it, overlapping the cover's
                  bottom edge. The cover always renders — a real banner image when uploaded, else a
                  brand-gradient fallback — so the avatar always has a backdrop to sit on. */}
              <div className="shop-hovercard__cover" data-testid="shop-hovercard-banner">
                {resolveMediaUrl(data.banner_url) && (
                  <img className="shop-hovercard__cover-img" src={resolveMediaUrl(data.banner_url)!} alt="" loading="lazy" />
                )}
                <ShopAvatar url={data.avatar_url} name={data.name} className="shop-hovercard__avatar" />
              </div>

              <div className="shop-hovercard__title">
                <h4 data-testid="shop-hovercard-name">{data.name}</h4>
                {data.rating != null ? (
                  <span className="shop-hovercard__rating">
                    ★ {data.rating.toFixed(1)} · {data.review_count} review{data.review_count === 1 ? '' : 's'}
                  </span>
                ) : (
                  <span className="shop-hovercard__rating shop-hovercard__rating--none">No reviews yet</span>
                )}
              </div>

              {data.contact && (
                <p className="shop-hovercard__contact" data-testid="shop-hovercard-contact">
                  <Icon name="phone" size={14} /> {data.contact}
                </p>
              )}

              {data.description && (
                <p className="shop-hovercard__desc" data-testid="shop-hovercard-desc">{data.description}</p>
              )}

              <p className="shop-hovercard__followers">
                {data.follower_count} follower{data.follower_count === 1 ? '' : 's'}
              </p>

              <div className="shop-hovercard__actions">
                <button
                  type="button"
                  className={`shop-hovercard__btn${data.following ? ' shop-hovercard__btn--following' : ' shop-hovercard__btn--primary'}`}
                  onClick={handleFollow}
                  disabled={!session || toggleFollow.isPending}
                  aria-pressed={data.following}
                  data-testid="shop-hovercard-notify"
                >
                  <Icon name="bell" size={15} />
                  {data.following ? 'Following' : 'Notify'}
                </button>
                <button
                  type="button"
                  className="shop-hovercard__btn"
                  onClick={handleProfile}
                  data-testid="shop-hovercard-profile"
                >
                  Store Front
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
};

export default ShopHoverCard;
