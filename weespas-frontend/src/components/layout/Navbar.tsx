/* ==========================================================================
   NAVBAR — Sticky top navigation
   Always solid white background. Hides on mobile scroll-down.
   "Help" trigger opens MegaMenu on desktop, expands inline in mobile drawer.
   ========================================================================== */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { useFavorites } from '../../hooks/useFavorites';
import { useRoleApplicationBadge } from '../../hooks/useRoleApplicationBadge';
import { openInsarRiskMap } from '../../api/insar';
import Icon from '../ui/Icon';
import MegaMenu from './MegaMenu';
import NavbarSearch from './NavbarSearch';
import NotificationBell from './NotificationBell';
import ProfileMenu from './ProfileMenu';
import './Navbar.css';

const Navbar: React.FC = () => {
  const { isAuthenticated, user, token } = useAuth();
  const { favoriteCount } = useFavorites();
  // Pending-application badge for admins. The hook is internally gated
  // to admin users only — for everyone else this is a no-op subscription
  // that fires zero network requests.
  const { data: appBadge } = useRoleApplicationBadge();
  const adminPendingTotal = (appBadge?.agent_pending ?? 0) + (appBadge?.staff_pending ?? 0);
  const location = useLocation();
  const navigate = useNavigate();
  const isHome = location.pathname === '/';
  const [hidden, setHidden] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [mobileHelpOpen, setMobileHelpOpen] = useState(false);
  const [lastScrollY, setLastScrollY] = useState(0);
  const helpWrapRef = useRef<HTMLDivElement>(null);
  const megaAnchorRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<number | null>(null);

  /* Hide navbar on mobile scroll-down */
  const handleScroll = useCallback(() => {
    const currentY = window.scrollY;

    if (window.innerWidth < 768) {
      setHidden(currentY > lastScrollY && currentY > 120);
    } else {
      setHidden(false);
    }
    setLastScrollY(currentY);
  }, [lastScrollY]);

  useEffect(() => {
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, [handleScroll]);

  /* Close drawer / mega menu on Escape */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setDrawerOpen(false);
        setHelpOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  /* Close mega menu on outside click */
  useEffect(() => {
    if (!helpOpen) return;
    const onClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        !helpWrapRef.current?.contains(target) &&
        !megaAnchorRef.current?.contains(target)
      ) {
        setHelpOpen(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [helpOpen]);

  /* Lock body scroll when drawer is open */
  useEffect(() => {
    document.body.style.overflow = drawerOpen ? 'hidden' : '';
    return () => { document.body.style.overflow = ''; };
  }, [drawerOpen]);

  /* Smooth-scroll to an element by ID, navigating home first if needed */
  const scrollToSection = useCallback((id: string) => {
    const doScroll = () => {
      const el = document.getElementById(id);
      if (el) {
        const offset = 20;
        const top = el.getBoundingClientRect().top + window.scrollY - offset;
        window.scrollTo({ top, behavior: 'smooth' });
      }
    };

    if (isHome) {
      doScroll();
    } else {
      navigate('/');
      setTimeout(doScroll, 100);
    }
  }, [isHome, navigate]);

  const openHelp = () => {
    if (closeTimer.current) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
    setHelpOpen(true);
  };

  const scheduleCloseHelp = () => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => setHelpOpen(false), 120);
  };

  const navClasses = [
    'navbar',
    hidden ? 'navbar--hidden' : '',
  ].filter(Boolean).join(' ');

  const closeMobileHelp = () => setMobileHelpOpen(false);
  const goQuickMessageMobile = () => {
    setDrawerOpen(false);
    closeMobileHelp();
    scrollToSection('contact-form');
  };

  return (
    <>
      <nav className={navClasses}>
        <div className="navbar__inner container">
          {/* Logo. The crosshair closes the wordmark, so it must read as one unit: the <span> is
              aria-hidden via Icon and sized in `em` by .brand-mark, and the accessible name stays
              plain "weespas" (screen readers must not announce a decorative glyph). */}
          <Link to="/" className="navbar__logo" aria-label="weespas — home">
            weespas
            <span className="brand-mark">
              <Icon name="crosshair" />
            </span>
          </Link>

          {/* Desktop nav links */}
          <div className="navbar__links">
            {/* Inline unified search box (Properties + Trade). Results drop as a list under the box;
                the Shops & Products section is gated on a signed-in commerce session inside. */}
            <NavbarSearch isAuthenticated={isAuthenticated} variant="inline" />

            <Link
              to="/"
              className={`navbar__icon-btn${isHome ? ' navbar__icon-btn--active' : ''}`}
              aria-label="Homes"
              title="Homes"
              aria-current={isHome ? 'page' : undefined}
            >
              <Icon name="grid" size={20} />
            </Link>

            {/* Properties is no longer a separate top-row item — "Homes" (above) is the entry to the
                property listings, and the hero's "Scroll Down" hint jumps to the section. Risk Map
                and Trade live in the Services mega menu (below) to keep the row uncluttered. */}

            {isAuthenticated && (
              <Link to="/favorites" className="navbar__icon-btn" aria-label="Favorites" title="Favorites">
                <Icon name="heart" size={20} />
                {favoriteCount > 0 && (
                  <span className="navbar__badge">{favoriteCount > 9 ? '9+' : favoriteCount}</span>
                )}
              </Link>
            )}

            {isAuthenticated && <NotificationBell />}

            {/* Services trigger (mega menu sits under the Sign Up CTA, see below) */}
            <div
              className="navbar__help-wrap"
              ref={helpWrapRef}
              onMouseEnter={openHelp}
              onMouseLeave={scheduleCloseHelp}
            >
              <button
                type="button"
                className={`navbar__icon-btn navbar__help-trigger${helpOpen ? ' is-open' : ''}`}
                aria-haspopup="true"
                aria-expanded={helpOpen}
                aria-label="Services"
                title="Services"
                onClick={() => setHelpOpen((v) => !v)}
                onFocus={openHelp}
              >
                <Icon name="services" size={28} />
              </button>
            </div>

            {isAuthenticated ? (
              <ProfileMenu user={user} pendingBadge={adminPendingTotal} />
            ) : (
              <>
                <Link to="/login" className="navbar__link">Login</Link>
                <Link to="/register" className="navbar__link navbar__link--cta">Sign Up</Link>
              </>
            )}

            {/* Mega menu — anchored to the right edge of .navbar__links (i.e. under the Sign Up CTA) */}
            <div
              className="navbar__mega-anchor"
              ref={megaAnchorRef}
              onMouseEnter={openHelp}
              onMouseLeave={scheduleCloseHelp}
            >
              <MegaMenu
                open={helpOpen}
                onClose={() => setHelpOpen(false)}
                token={token}
                isAuthenticated={isAuthenticated}
              />
            </div>
          </div>

          {/* Mobile-only right cluster: search + hamburger (grouped so they sit together on the
              right rather than splitting under .navbar__inner's space-between). */}
          <div className="navbar__mobile-actions">
            <button
              type="button"
              className="navbar__mobile-search"
              onClick={() => setSearchOpen(true)}
              aria-label="Search"
            >
              <Icon name="search" size={22} />
            </button>

            <button
              type="button"
              className="navbar__hamburger"
              onClick={() => setDrawerOpen(true)}
              aria-label="Open menu"
            >
              <span className="navbar__hamburger-line" />
              <span className="navbar__hamburger-line" />
              <span className="navbar__hamburger-line" />
            </button>
          </div>
        </div>
      </nav>

      {/* Mobile Drawer Overlay */}
      {drawerOpen && (
        <div className="drawer-backdrop" onClick={() => setDrawerOpen(false)} />
      )}

      {/* Mobile Drawer Panel */}
      <aside className={`drawer ${drawerOpen ? 'drawer--open' : ''}`} role="dialog" aria-modal="true">
        <div className="drawer__header">
          <span className="drawer__title">Menu</span>
          <button
            type="button"
            className="drawer__close"
            onClick={() => setDrawerOpen(false)}
            aria-label="Close menu"
          >
            <Icon name="x" size={24} />
          </button>
        </div>

        <nav className="drawer__nav">
          <Link to="/" className="drawer__link" onClick={() => setDrawerOpen(false)}>
            <Icon name="grid" size={20} />
            <span>Homes</span>
          </Link>
          {isAuthenticated && (
            <Link to="/favorites" className="drawer__link" onClick={() => setDrawerOpen(false)}>
              <Icon name="heart" size={20} />
              <span>Favorites</span>
              {favoriteCount > 0 && (
                <span className="drawer__badge">{favoriteCount > 9 ? '9+' : favoriteCount}</span>
              )}
            </Link>
          )}

          {/* Services — collapsible group (mirrors the desktop mega menu) */}
          <button
            type="button"
            className={`drawer__link drawer__link--group${mobileHelpOpen ? ' is-open' : ''}`}
            onClick={() => setMobileHelpOpen((v) => !v)}
            aria-expanded={mobileHelpOpen}
          >
            <Icon name="services" size={22} />
            <span>Services</span>
            <Icon name="chevronRight" size={16} className="drawer__group-chevron" />
          </button>
          {mobileHelpOpen && (
            <div className="drawer__sublist">
              <Link
                to="/customer-care"
                className="drawer__sublink"
                onClick={() => setDrawerOpen(false)}
              >
                <Icon name="supportAgent" size={18} />
                <span>
                  <strong>Customer Care</strong>
                  <em>24Hr support &mdash; consultation &amp; reports</em>
                </span>
              </Link>
              <button
                type="button"
                className="drawer__sublink"
                onClick={goQuickMessageMobile}
              >
                <Icon name="edit" size={18} />
                <span>
                  <strong>Quick Message</strong>
                  <em>Send a quick message, reply in 24hrs</em>
                </span>
              </button>
              <Link
                to="/agents"
                className="drawer__sublink"
                onClick={() => setDrawerOpen(false)}
              >
                <Icon name="user" size={18} />
                <span>
                  <strong>Agents</strong>
                  <em>Browse our verified property experts</em>
                </span>
              </Link>
              {/* Risk Map — InSAR subsidence map (free, login-required). */}
              <button
                type="button"
                className="drawer__sublink"
                onClick={() => { setDrawerOpen(false); void openInsarRiskMap(token, navigate); }}
              >
                <Icon name="map" size={18} />
                <span>
                  <strong>InSAR</strong>
                  <em>Is your building sinking? Check the map</em>
                </span>
              </button>
              {/* Trade — proximity marketplace (authed-only: commerce needs a signed-in user). */}
              {isAuthenticated && (
                <Link
                  to="/trade"
                  className="drawer__sublink"
                  onClick={() => setDrawerOpen(false)}
                >
                  <Icon name="trade" size={18} />
                  <span>
                    <strong>Trade</strong>
                    <em>What’s selling near you</em>
                  </span>
                </Link>
              )}
            </div>
          )}

          {isAuthenticated ? (
            <Link to="/profile" className="drawer__link" onClick={() => setDrawerOpen(false)}>
              <Icon name="user" size={20} />
              <span>Profile</span>
              {adminPendingTotal > 0 && (
                <span className="drawer__badge" aria-label={`${adminPendingTotal} pending applications`}>
                  {adminPendingTotal > 9 ? '9+' : adminPendingTotal}
                </span>
              )}
            </Link>
          ) : (
            <>
              <Link to="/login" className="drawer__link" onClick={() => setDrawerOpen(false)}>
                <Icon name="user" size={20} />
                <span>Login</span>
              </Link>
              <Link to="/register" className="drawer__link" onClick={() => setDrawerOpen(false)}>
                <Icon name="user" size={20} />
                <span>Sign Up</span>
              </Link>
            </>
          )}
        </nav>

        <div className="drawer__footer">
          <p>+254 713 083 378</p>
          <p>hello@weespas.com</p>
        </div>
      </aside>

      {/* Mobile unified search — the navbar row is too tight for an inline box, so the magnifier
          (in .navbar__mobile-actions) expands this full-width bar under the navbar. Same component,
          overlay variant; the Shops & Products section is gated on a signed-in session inside. */}
      {searchOpen && (
        <NavbarSearch
          isAuthenticated={isAuthenticated}
          variant="overlay"
          onClose={() => setSearchOpen(false)}
        />
      )}
    </>
  );
};

export default Navbar;
