import React, { Suspense, lazy, useCallback, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import { useFavorites } from '../hooks/useFavorites';
import { useMe } from '../hooks/useMe';
import { useRoleEligibility, ROLE_ELIGIBILITY_KEY } from '../hooks/useRoleEligibility';
import { submitRoleApplication, type RoleApplicationRole } from '../api/roleApplications';
import Icon from '../components/ui/Icon';
import PageMeta from '../components/ui/PageMeta';
import { hasRole } from '../utils/roles';
import { resolveMediaUrl } from '../utils/media';
import './ProfilePage.css';

// Phase 1+ profile panels are lazy-loaded so users who never open them pay
// zero bundle cost on the main profile screen.
const PreferencesPanel = lazy(() => import('../components/profile/PreferencesPanel'));
const EditProfilePanel = lazy(() => import('../components/profile/EditProfilePanel'));
// RoleApplicationModal is lazy too — a user who never clicks "Become …"
// shouldn't download its (small) bundle.
const RoleApplicationModal = lazy(() => import('../components/profile/RoleApplicationModal'));

type PanelView = 'main' | 'preferences' | 'edit';

const ProfilePage: React.FC = () => {
  const navigate = useNavigate();
  const { isAuthenticated, logout, token } = useAuth();
  const queryClient = useQueryClient();
  // Prefer the React Query cache for live user data so the privacy toggle
  // (and future profile edits) reflect immediately. Fall back to AuthContext
  // when useMe hasn't resolved yet (first paint after a cold reload).
  const { data: meData } = useMe();
  const { user: contextUser } = useAuth();
  const user = meData ?? contextUser;
  const { favoriteCount } = useFavorites();
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const [view, setView] = useState<PanelView>('main');

  // ── Role-application state ─────────────────────────────────────────
  // Subscribes to the precomputed eligibility HASH. Server-side cost is
  // one Redis HGET; client-side cache is keyed by ['auth','roleEligibility']
  // with a 60s staleTime so we don't re-fire on every tab focus.
  const { data: eligibility } = useRoleEligibility();
  const [applyRole, setApplyRole] = useState<RoleApplicationRole | null>(null);
  const [applySubmitting, setApplySubmitting] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);

  const handleSubmitApplication = useCallback(async (message: string) => {
    if (!token || !applyRole) return;
    setApplySubmitting(true);
    setApplyError(null);
    try {
      await submitRoleApplication(token, applyRole, message);
      // Invalidate so the just-submitted CTA hides on next render — the
      // server now returns pending_agent/pending_staff = true.
      await queryClient.invalidateQueries({ queryKey: ROLE_ELIGIBILITY_KEY });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Could not submit application.';
      setApplyError(msg);
      // Re-throw so the modal stays in the form state on error.
      throw err;
    } finally {
      setApplySubmitting(false);
    }
  }, [token, applyRole, queryClient]);

  const closeApplyModal = useCallback(() => {
    setApplyRole(null);
    setApplyError(null);
    setApplySubmitting(false);
  }, []);

  // Redirect unauthenticated users
  if (!isAuthenticated || !user) {
    return (
      <div className="profile-page">
        <PageMeta title="Profile" description="Manage your Weespas profile, view saved properties, and update your preferences." />
        <div className="profile-card">
          <div className="profile-header">
            <Link to="/" className="profile-logo">weespas</Link>
            <h1>Your Profile</h1>
            <p>Sign in to view your profile, saved properties, and preferences.</p>
          </div>
          <div className="profile-auth-cta">
            <Link to="/login" className="profile-btn profile-btn--primary">
              Sign In
            </Link>
            <Link to="/register" className="profile-btn profile-btn--secondary">
              Create Account
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const initials = user.name
    .split(' ')
    .map((n) => n[0])
    .join('')
    .toUpperCase()
    .slice(0, 2);

  const memberSince = new Date(user.created_at).toLocaleDateString('en-KE', {
    month: 'long',
    year: 'numeric',
  });

  const handleLogout = () => {
    logout();
    navigate('/');
  };

  if (view === 'preferences') {
    return (
      <div className="profile-page">
        <PageMeta title="Preferences" description="Manage your Weespas privacy and notification preferences." />
        <Suspense fallback={<div className="profile-card" aria-busy="true" />}>
          <PreferencesPanel onClose={() => setView('main')} />
        </Suspense>
      </div>
    );
  }

  if (view === 'edit') {
    return (
      <div className="profile-page">
        <PageMeta title="Edit Profile" description="Update your Weespas avatar, name, phone, email and password." />
        <Suspense fallback={<div className="profile-card" aria-busy="true" />}>
          <EditProfilePanel onClose={() => setView('main')} />
        </Suspense>
      </div>
    );
  }

  return (
    <div className="profile-page">
      <PageMeta title="My Profile" description="Manage your Weespas profile, view saved properties, and update your preferences." />
      <div className="profile-card">
        {/* Header */}
        <div className="profile-header">
          <Link to="/" className="profile-logo">weespas</Link>
          <h1>My Profile</h1>
        </div>

        {/* Avatar & Name */}
        <div className="profile-identity">
          <div className="profile-avatar">
            {user.avatar ? (
              // Backend stores root-relative `/uploads/avatars/...`; the static
              // mount lives on the backend origin, not the frontend origin, so
              // we route through resolveMediaUrl (matches PropertyCard et al.).
              <img src={resolveMediaUrl(user.avatar)} alt={user.name} className="profile-avatar__img" />
            ) : (
              <span className="profile-avatar__initials">{initials}</span>
            )}
          </div>
          <h2 className="profile-identity__name">{user.name}</h2>
          <p className="profile-identity__since">Member since {memberSince}</p>
        </div>

        {/* Contact Info */}
        <div className="profile-section">
          <h3 className="profile-section__title">Contact Information</h3>
          <div className="profile-info-list">
            <div className="profile-info-item">
              <Icon name="mail" size={18} />
              <div className="profile-info-item__content">
                <span className="profile-info-item__label">Email</span>
                <span className="profile-info-item__value">{user.email}</span>
              </div>
            </div>
            <div className="profile-info-item">
              <Icon name="phone" size={18} />
              <div className="profile-info-item__content">
                <span className="profile-info-item__label">Phone</span>
                <span className="profile-info-item__value">+254 {user.phone}</span>
              </div>
            </div>
          </div>
        </div>

        {/* Stats */}
        <div className="profile-section">
          <h3 className="profile-section__title">Activity</h3>
          <div className="profile-stats">
            <Link to="/favorites" className="profile-stat-card">
              <div className="profile-stat-card__icon">
                <Icon name="heart" size={20} />
              </div>
              <span className="profile-stat-card__count">{favoriteCount}</span>
              <span className="profile-stat-card__label">Saved Properties</span>
            </Link>
            <div className="profile-stat-card">
              <div className="profile-stat-card__icon">
                <Icon name="search" size={20} />
              </div>
              <span className="profile-stat-card__count">--</span>
              <span className="profile-stat-card__label">Searches</span>
            </div>
          </div>
        </div>

        {/* Settings / Quick Links */}
        <div className="profile-section">
          <h3 className="profile-section__title">Settings</h3>
          <div className="profile-menu">
            {hasRole(user, 'admin') && (
              <>
                <Link to="/admin" className="profile-menu-item profile-menu-item--agent">
                  <Icon name="settings" size={18} />
                  <span>Admin Panel</span>
                  <Icon name="chevronRight" size={16} />
                </Link>
                <Link to="/stats?view=admin" className="profile-menu-item profile-menu-item--agent">
                  <Icon name="barChart" size={18} />
                  <span>Agent Dashboard</span>
                  <Icon name="chevronRight" size={16} />
                </Link>
              </>
            )}
            {hasRole(user, 'staff') && (
              <Link to="/staff" className="profile-menu-item profile-menu-item--agent">
                <Icon name="barChart" size={18} />
                <span>Staff Dashboard</span>
                <Icon name="chevronRight" size={16} />
              </Link>
            )}
            {hasRole(user, 'agent') && (
              <Link to="/stats?view=agent" className="profile-menu-item profile-menu-item--agent">
                <Icon name="barChart" size={18} />
                <span>Agent Dashboard</span>
                <Icon name="chevronRight" size={16} />
              </Link>
            )}
            {/* "Become …" CTAs.
             *
             * The eligibility object decides whether each CTA renders:
             *   - `agent_eligible` is true iff the user is not yet an
             *     agent AND has no pending agent application.
             *   - The Become-Staff CTA renders whenever the user IS an
             *     agent and is not yet staff (the modal itself handles
             *     the eligible / ineligible branching internally so the
             *     user always discovers the requirements via the popup).
             *
             * Both buttons collapse to a single "Application pending
             * review" indicator while a submission is in flight.
             */}
            {eligibility?.pending_agent && (
              <div className="profile-menu-item profile-menu-item--pending" aria-live="polite">
                <Icon name="user" size={18} />
                <span>Agent application pending review</span>
              </div>
            )}
            {eligibility?.agent_eligible && (
              <button
                type="button"
                className="profile-menu-item"
                onClick={() => setApplyRole('agent')}
              >
                <Icon name="user" size={18} />
                <span>Become an Agent</span>
                <Icon name="chevronRight" size={16} />
              </button>
            )}
            {eligibility?.pending_staff && (
              <div className="profile-menu-item profile-menu-item--pending" aria-live="polite">
                <Icon name="verified" size={18} />
                <span>Staff application pending review</span>
              </div>
            )}
            {/* Show "Become Staff" whenever the user is an agent but not
             *  yet staff and has no pending staff application. The
             *  ineligible state is handled inside the modal so the user
             *  always discovers the requirements (rather than seeing
             *  nothing and being confused). */}
            {user.agent_id
              && !hasRole(user, 'staff')
              && !eligibility?.pending_staff && (
              <button
                type="button"
                className="profile-menu-item"
                onClick={() => setApplyRole('staff')}
              >
                <Icon name="verified" size={18} />
                <span>Become Staff</span>
                <Icon name="chevronRight" size={16} />
              </button>
            )}
            <Link to="/favorites" className="profile-menu-item">
              <Icon name="heart" size={18} />
              <span>Saved Properties</span>
              <Icon name="chevronRight" size={16} />
            </Link>
            <button
              type="button"
              className="profile-menu-item"
              onClick={() => setView('preferences')}
            >
              <Icon name="settings" size={18} />
              <span>Preferences</span>
              <Icon name="chevronRight" size={16} />
            </button>
            <button
              type="button"
              className="profile-menu-item"
              onClick={() => setView('edit')}
            >
              <Icon name="edit" size={18} />
              <span>Edit Profile</span>
              <Icon name="chevronRight" size={16} />
            </button>
          </div>
        </div>

        {/* Role-application modal — rendered into a portal by the
         *  component itself, so its position in this JSX tree doesn't
         *  affect layout. Lazy-loaded (see import at top) so the bundle
         *  cost is paid only on first click. */}
        {applyRole && (
          <Suspense fallback={null}>
            <RoleApplicationModal
              isOpen={!!applyRole}
              role={applyRole}
              staffStats={eligibility?.staff_stats ?? null}
              isEligible={
                applyRole === 'agent'
                  ? !!eligibility?.agent_eligible
                  : !!eligibility?.staff_eligible
              }
              isLoading={applySubmitting}
              errorMessage={applyError}
              onClose={closeApplyModal}
              onSubmit={handleSubmitApplication}
            />
          </Suspense>
        )}

        {/* Logout */}
        <div className="profile-section profile-section--logout">
          {!showLogoutConfirm ? (
            <button
              type="button"
              className="profile-btn profile-btn--logout"
              onClick={() => setShowLogoutConfirm(true)}
            >
              <Icon name="logout" size={18} />
              Sign Out
            </button>
          ) : (
            <div className="profile-logout-confirm">
              <p>Are you sure you want to sign out?</p>
              <div className="profile-logout-confirm__actions">
                <button
                  type="button"
                  className="profile-btn profile-btn--secondary"
                  onClick={() => setShowLogoutConfirm(false)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="profile-btn profile-btn--danger"
                  onClick={handleLogout}
                >
                  Sign Out
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ProfilePage;
