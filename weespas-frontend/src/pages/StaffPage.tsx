import React, { Suspense, lazy, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import Icon from '../components/ui/Icon';
import UnifiedSearchPanel from '../components/ui/UnifiedSearchPanel';
import StaffDirectoryCard from '../components/staff/StaffDirectoryCard';
import RiskTileCard from '../components/analytics/RiskTileCard';
import FlagReviewQueueCard from '../components/staff/FlagReviewQueueCard';
import SponsoredCapQueueCard from '../components/staff/SponsoredCapQueueCard';
import PageMeta from '../components/ui/PageMeta';
import { useStaffDeletionRequests } from '../hooks/useStaffDeletionRequests';
import { isStaffOrAdmin as checkStaffOrAdmin } from '../utils/roles';
import { formatDate } from '../utils/format';
import './StatsPage.css';
import './AdminPage.css';

// Recharts is ~130 KB gzipped — lazy-load it so the Staff dashboard's first
// paint isn't gated on it. The Suspense fallback renders a lightweight
// placeholder card matching the eventual chart's height to avoid layout shift.
const EngagementCharts = lazy(() => import('../components/analytics/EngagementCharts'));

const StaffPage: React.FC = () => {
  const { user, token, isAuthenticated } = useAuth();
  const [showSearch, setShowSearch] = useState(false);

  if (!isAuthenticated || !user || !checkStaffOrAdmin(user)) {
    return <Navigate to="/profile" replace />;
  }

  const {
    data: drData,
    isLoading: drLoading,
    isError: drError,
  } = useStaffDeletionRequests(token);

  const drItems = drData?.items ?? [];

  return (
    <div className="stats-page admin-page">
      <PageMeta
        title="Staff Dashboard"
        description="Moderation tools for Weespas staff — browse users and agents and track your own deletion requests."
      />
      <div className="stats-container">
        <Link to="/profile" className="stats-back">
          <Icon name="arrowLeft" size={18} />
          <span>Back to Profile</span>
        </Link>

        <header className="stats-header">
          <div className="stats-header__top">
            <h1>Staff Dashboard</h1>
            <span className="stats-header__role">Staff</span>
          </div>
          <p className="stats-header__subtitle">
            Search users and agents, request deletions, and follow up on your requests.
          </p>
        </header>

        <StaffDirectoryCard
          token={token}
          onOpenSearch={() => setShowSearch(true)}
        />

        <RiskTileCard token={token} />

        <FlagReviewQueueCard />

        <SponsoredCapQueueCard />

        <Suspense fallback={<div className="chart-card chart-card--loading">Loading engagement charts…</div>}>
          <EngagementCharts token={token} />
        </Suspense>

        {/* Personal deletion requests — backend scopes to requested_by_id == current_user.id */}
        <section className="stats-deletion-requests stats-staff-requests">
          <div className="stats-deletion-requests__header">
            <div>
              <h3 className="stats-deletion-requests__title">
                My Deletion Requests
                {drItems.length > 0 && (
                  <span className="stats-properties__count">{drItems.length}</span>
                )}
              </h3>
              <p className="admin-section__hint">
                Deletion requests must first be reviewed by Admin
              </p>
            </div>
          </div>
          {drLoading ? (
            <div className="stats-chart-empty">Loading your requests…</div>
          ) : drError ? (
            <div className="stats-properties-error">
              <Icon name="alertTriangle" size={18} />
              <p>Failed to load your deletion requests.</p>
            </div>
          ) : drItems.length === 0 ? (
            <div className="stats-chart-empty">
              You haven't submitted any deletion requests yet.
            </div>
          ) : (
            <div className="stats-dr-list">
              {drItems.map((req) => (
                <div key={req.id} className="stats-dr-card">
                  <div className="stats-dr-card__info">
                    <p className="stats-dr-card__target">Target: {req.target_user_name ?? req.target_user_id ?? 'Deleted user'}</p>
                    <p className="stats-dr-card__reason">{req.reason}</p>
                    <p className="stats-dr-card__date">{formatDate(req.created_at)}</p>
                  </div>
                  <div className="stats-dr-card__status">
                    <span className={`stats-dr-status stats-dr-status--${req.status}`}>
                      {req.status}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      <UnifiedSearchPanel
        isOpen={showSearch}
        onClose={() => setShowSearch(false)}
        token={token}
        userRole={user.role}
        currentUserId={user.id}
      />
    </div>
  );
};

export default StaffPage;
