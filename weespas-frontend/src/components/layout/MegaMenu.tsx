/* ==========================================================================
   MEGA MENU — Desktop "Help" dropdown
   3-column panel: Customer Care · Quick Message · Agents.
   Hidden on mobile; the Navbar drawer renders these as plain links.
   ========================================================================== */

import React, { useEffect, useRef } from 'react';
import { Link, useNavigate, useLocation } from 'react-router-dom';
import Icon from '../ui/Icon';
import { openInsarRiskMap } from '../../api/insar';
import './MegaMenu.css';

interface MegaMenuProps {
  open: boolean;
  onClose: () => void;
  /** Weespas auth token — needed to deep-link the Risk Map with a telemetry session. */
  token: string | null;
  /** Gates the Trade entry (commerce needs a signed-in user to mint a commerce token). */
  isAuthenticated: boolean;
}

const MegaMenu: React.FC<MegaMenuProps> = ({ open, onClose, token, isAuthenticated }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const handleQuickMessage = () => {
    onClose();
    if (location.pathname === '/') {
      document.getElementById('contact-form')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } else {
      navigate('/#contact-form');
      setTimeout(() => {
        document.getElementById('contact-form')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 100);
    }
  };

  return (
    <div
      ref={panelRef}
      className={`mega-menu${open ? ' mega-menu--open' : ''}`}
      role="menu"
      aria-hidden={!open}
    >
      <div className="mega-menu__inner">
        {/* Risk Map — InSAR subsidence map (free, login-required). Authed users get a
            telemetry-linked deep-link; anon users route to login and resume after sign-in. */}
        <button
          type="button"
          className="mega-menu__col"
          role="menuitem"
          onClick={() => { onClose(); void openInsarRiskMap(token, navigate); }}
        >
          <span className="mega-menu__icon">
            <Icon name="map" size={26} />
          </span>
          <span className="mega-menu__heading">InSAR</span>
          <span className="mega-menu__copy">
            Is your building sinking? See ground-movement risk on the InSAR map.
          </span>
        </button>

        {/* Trade — the proximity marketplace. Authed-only (commerce needs a signed-in user). */}
        {isAuthenticated && (
          <Link
            to="/trade"
            className="mega-menu__col"
            role="menuitem"
            onClick={onClose}
          >
            <span className="mega-menu__icon">
              <Icon name="trade" size={24} />
            </span>
            <span className="mega-menu__heading">Trade</span>
            <span className="mega-menu__copy">
              What’s selling near you — discover neighbours’ shops and fresh stock.
            </span>
          </Link>
        )}

        {/* Sell — the seller console. Every house a shop (§9): any signed-in user can sell. */}
        {isAuthenticated && (
          <Link
            to="/trade/sell"
            className="mega-menu__col"
            role="menuitem"
            onClick={onClose}
          >
            <span className="mega-menu__icon">
              <Icon name="plus" size={24} />
            </span>
            <span className="mega-menu__heading">Sell on Weespas</span>
            <span className="mega-menu__copy">
              Open a shop and list what you’re selling — neighbours discover it instantly.
            </span>
          </Link>
        )}

        <Link
          to="/customer-care"
          className="mega-menu__col"
          role="menuitem"
          onClick={onClose}
        >
          <span className="mega-menu__icon">
            <Icon name="supportAgent" size={26} />
          </span>
          <span className="mega-menu__heading">Customer Care</span>
          <span className="mega-menu__copy">
            24Hr Customer service. Get help now on any consultation or reports.
          </span>
        </Link>

        <button
          type="button"
          className="mega-menu__col"
          role="menuitem"
          onClick={handleQuickMessage}
        >
          <span className="mega-menu__icon">
            <Icon name="edit" size={24} />
          </span>
          <span className="mega-menu__heading">Quick Message</span>
          <span className="mega-menu__copy">
            Share with us a quick message, application or proposal. We reply in 24hrs.
          </span>
        </button>

        <Link
          to="/agents"
          className="mega-menu__col"
          role="menuitem"
          onClick={onClose}
        >
          <span className="mega-menu__icon">
            <Icon name="user" size={24} />
          </span>
          <span className="mega-menu__heading">Agents</span>
          <span className="mega-menu__copy">
            Browse our verified property experts and connect with the right agent for you.
          </span>
        </Link>
      </div>
    </div>
  );
};

export default MegaMenu;
