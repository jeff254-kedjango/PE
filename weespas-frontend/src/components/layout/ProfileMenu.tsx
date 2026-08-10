// ProfileMenu — the signed-in account chip in the navbar's CTA slot.
//
// Replaces the old "first-name" text link. Shows the user's avatar (profile picture, or a brand
// initials fallback via ShopAvatar) where the "Sign Up" CTA sits for logged-out visitors. The
// avatar itself never navigates — hovering it (desktop) / tapping it (touch) reveals a small popup
// with a single "My Profile" link, and ONLY that link routes to /profile. Mirrors the hover/touch
// mechanics of ShopHoverCard (the app's canonical popover): pointer:coarse ⇒ tap-toggle, Esc +
// outside-pointerdown dismiss, a short close-delay so the cursor can travel avatar → panel.
import React, { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import ShopAvatar from '../trade/ShopAvatar';
import type { User } from '../../types/auth';
import './ProfileMenu.css';

interface ProfileMenuProps {
  user: User | null;
  /** Admin pending-application count (surfaced as a badge on the avatar); 0 hides it. */
  pendingBadge?: number;
}

// Coarse pointer ⇒ touch device: trigger on tap, not hover. Evaluated once at mount and guarded for
// non-browser test envs (same idiom as ShopHoverCard.isCoarsePointer).
function isCoarsePointer(): boolean {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(pointer: coarse)').matches;
}

const ProfileMenu: React.FC<ProfileMenuProps> = ({ user, pendingBadge = 0 }) => {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<number | null>(null);
  const coarse = useRef(isCoarsePointer());
  const panelId = useId();

  const cancelClose = () => {
    if (closeTimer.current) { window.clearTimeout(closeTimer.current); closeTimer.current = null; }
  };
  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = window.setTimeout(() => setOpen(false), 140);
  };
  useEffect(() => cancelClose, []);

  // Outside-tap + Esc dismiss (primary path on touch, backstop on desktop).
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

  // Touch: the trigger toggles the popup (the avatar never navigates on its own).
  const onTriggerClick = () => { if (coarse.current) setOpen((o) => !o); };

  return (
    <div className="profile-menu" ref={wrapRef} {...hoverProps}>
      <button
        type="button"
        className="profile-menu__trigger"
        onClick={onTriggerClick}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-label="Account menu"
        data-testid="navbar-avatar-trigger"
      >
        <ShopAvatar url={user?.avatar} name={user?.name} className="navbar__avatar" />
        {pendingBadge > 0 && (
          <span
            className="navbar__badge navbar__badge--cta"
            aria-label={`${pendingBadge} pending applications`}
          >
            {pendingBadge > 9 ? '9+' : pendingBadge}
          </span>
        )}
      </button>

      {open && (
        <div
          id={panelId}
          className="profile-menu__panel"
          role="menu"
          aria-label="Account"
          data-testid="navbar-profile-menu"
        >
          <Link
            to="/profile"
            className="profile-menu__item"
            role="menuitem"
            onClick={() => setOpen(false)}
          >
            My Profile
          </Link>
        </div>
      )}
    </div>
  );
};

export default ProfileMenu;
