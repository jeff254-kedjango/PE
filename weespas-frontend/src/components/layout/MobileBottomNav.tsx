/* ==========================================================================
   MOBILE BOTTOM NAV — Fixed bottom tab bar (visible < 768px)
   4 tabs: Search, Map, Favorites, Profile. Electric Lime active indicator.
   ========================================================================== */

import React from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { openInsarRiskMap } from '../../api/insar';
import Icon from '../ui/Icon';
import type { ViewMode } from '../ui/ViewToggle';
import './MobileBottomNav.css';

interface MobileBottomNavProps {
  favoriteCount?: number;
  viewMode?: ViewMode;
  onMapToggle?: () => void;
}

const MobileBottomNav: React.FC<MobileBottomNavProps> = ({
  favoriteCount = 0,
  viewMode = 'list',
  onMapToggle,
}) => {
  const location = useLocation();
  const navigate = useNavigate();
  const path = location.pathname;
  const { token } = useAuth();

  const tabs = [
    { to: '/', icon: 'search' as const, label: 'Search' },
    { to: '/#map', icon: 'map' as const, label: 'Map', isMapTab: true },
    { to: '#risk', icon: 'home' as const, label: 'Risk', isRiskTab: true },
    { to: '/favorites', icon: 'heart' as const, label: 'Saved', count: favoriteCount },
    { to: '/profile', icon: 'user' as const, label: 'Profile' },
  ];

  return (
    <nav className="bottom-nav" aria-label="Mobile navigation">
      {tabs.map((tab) => {
        const isMapTab = 'isMapTab' in tab && tab.isMapTab;
        const isRiskTab = 'isRiskTab' in tab && tab.isRiskTab;
        const isActive = isMapTab
          ? viewMode === 'map'
          : isRiskTab
            ? false   // opens an external map in a new tab — never the active route
            : tab.to === '/'
              ? path === '/' && viewMode !== 'map'
              : path.startsWith(tab.to);

        if (isRiskTab) {
          return (
            <button
              key={tab.label}
              type="button"
              className="bottom-nav__tab"
              onClick={() => { void openInsarRiskMap(token, navigate); }}
              aria-label="Risk Map"
            >
              <span className="bottom-nav__icon">
                <Icon name={tab.icon} size={22} />
              </span>
              <span className="bottom-nav__label">{tab.label}</span>
            </button>
          );
        }

        if (isMapTab) {
          return (
            <button
              key={tab.label}
              type="button"
              className={`bottom-nav__tab ${isActive ? 'bottom-nav__tab--active' : ''}`}
              onClick={onMapToggle}
            >
              <span className="bottom-nav__icon">
                <Icon name={tab.icon} size={22} />
              </span>
              <span className="bottom-nav__label">{tab.label}</span>
            </button>
          );
        }

        return (
          <Link
            key={tab.label}
            to={tab.to}
            className={`bottom-nav__tab ${isActive ? 'bottom-nav__tab--active' : ''}`}
          >
            <span className="bottom-nav__icon">
              <Icon name={tab.icon} size={22} />
              {tab.count !== undefined && tab.count > 0 && (
                <span className="bottom-nav__badge">{tab.count > 9 ? '9+' : tab.count}</span>
              )}
            </span>
            <span className="bottom-nav__label">{tab.label}</span>
          </Link>
        );
      })}
    </nav>
  );
};

export default MobileBottomNav;
