import { useState } from 'react';
import { resolveMediaUrl } from '../../utils/media';
import './ShopAvatar.css';

interface ShopAvatarProps {
  /** Shop avatar media URL (absolute or /uploads/... relative). null/undefined ⇒ initials fallback. */
  url?: string | null;
  /** Name the initial is derived from (the SHOP's name). Falls back to a neutral glyph if empty. */
  name?: string | null;
  /** Caller's sizing class (e.g. product-card__avatar / shop-hovercard__avatar) — keeps each
   *  surface's existing dimensions; this component only owns the img-vs-initials decision. */
  className?: string;
}

/** The shop's profile picture, shared by the feed card header and the shop hovercard. Renders the
 *  image when a URL is present (and loads cleanly); otherwise — no URL, or the image 404s/errors —
 *  it shows the initials circle the app used before. One implementation so the two surfaces can't
 *  drift, and a broken image URL never leaves an empty hole. Decorative: aria-hidden, the seller
 *  identity is conveyed by the adjacent name/label text. */
export default function ShopAvatar({ url, name, className = '' }: ShopAvatarProps) {
  const [broken, setBroken] = useState(false);
  const resolved = resolveMediaUrl(url);
  const initial = (name || '').trim().slice(0, 1).toUpperCase() || '•';

  if (resolved && !broken) {
    return (
      <img
        src={resolved}
        alt=""
        aria-hidden="true"
        loading="lazy"
        className={`shop-avatar ${className}`}
        onError={() => setBroken(true)}
        data-testid="shop-avatar-img"
      />
    );
  }
  return (
    <span className={`shop-avatar shop-avatar--initial ${className}`} aria-hidden="true" data-testid="shop-avatar-initial">
      {initial}
    </span>
  );
}
