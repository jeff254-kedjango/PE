import React, { useCallback, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import Icon from '../components/ui/Icon';
import UnifiedSearchPanel from '../components/ui/UnifiedSearchPanel';
import FeaturedManager from '../components/admin/FeaturedManager';
import PageMeta from '../components/ui/PageMeta';
import { useDeletionRequests } from '../hooks/useDeletionRequests';
import { handleDeletionRequest } from '../api/admin';
import { useRoleApplications } from '../hooks/useRoleApplications';
import { reviewRoleApplication, type RoleApplicationRole } from '../api/roleApplications';
import { hasRole } from '../utils/roles';
import { formatDate } from '../utils/format';
import './StatsPage.css';
import './AdminPage.css';

const AdminPage: React.FC = () => {
  const { user, token, isAuthenticated } = useAuth();
  const [showSearch, setShowSearch] = useState(false);
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [processingDr, setProcessingDr] = useState<string | null>(null);
  const [drFilter, setDrFilter] = useState<'pending' | 'approved'>('pending');
  // Role-application queue tabs — mirrors the deletion-requests pattern.
  const [appStatusFilter, setAppStatusFilter] = useState<'pending' | 'approved'>('pending');
  const [appRoleFilter, setAppRoleFilter] = useState<'all' | RoleApplicationRole>('all');
  const [processingApp, setProcessingApp] = useState<string | null>(null);

  const isAdmin = !!user && hasRole(user, 'admin');

  const {
    data: drData,
    isLoading: drLoading,
    isError: drError,
  } = useDeletionRequests(isAdmin ? token : null, drFilter);

  // Subscribe to the role-application queue. Filter params keyed into
  // the query key so React Query caches each tab combination separately.
  const {
    data: appData,
    isLoading: appLoading,
    isError: appError,
  } = useRoleApplications(isAdmin ? token : null, {
    status: appStatusFilter,
    role: appRoleFilter === 'all' ? undefined : appRoleFilter,
  });

  const handleAppDecision = useCallback(
    async (applicationId: string, decision: 'approved' | 'rejected') => {
      if (!token) return;
      setProcessingApp(applicationId);
      try {
        await reviewRoleApplication(token, applicationId, { status: decision });
        toast.success(`Application ${decision}`);
        // Refresh both the list and the NavBar badge counter.
        queryClient.invalidateQueries({ queryKey: ['roleApplications'] });
        queryClient.invalidateQueries({ queryKey: ['roleApplicationBadge'] });
        if (decision === 'approved') {
          // Approval flips roles + agent_id — surfaces like the agent
          // directory and unified search must refetch.
          queryClient.invalidateQueries({ queryKey: ['unifiedSearch'] });
          queryClient.invalidateQueries({ queryKey: ['publicAgents'] });
          queryClient.invalidateQueries({ queryKey: ['agentProfile'] });
        }
      } catch (err) {
        toast.error(err instanceof Error ? err.message : `Failed to ${decision} application`);
      } finally {
        setProcessingApp(null);
      }
    },
    [token, toast, queryClient],
  );

  const handleDrDecision = useCallback(async (requestId: string, decision: 'approved' | 'rejected') => {
    if (!token) return;
    setProcessingDr(requestId);
    try {
      await handleDeletionRequest(token, requestId, { status: decision });
      toast.success(`Deletion request ${decision}`);
      queryClient.invalidateQueries({ queryKey: ['deletionRequests'] });
      queryClient.invalidateQueries({ queryKey: ['staffDeletionRequests'] });
      if (decision === 'approved') {
        queryClient.invalidateQueries({ queryKey: ['unifiedSearch'] });
        queryClient.invalidateQueries({ queryKey: ['agentStats'] });
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : `Failed to ${decision} request`);
    } finally {
      setProcessingDr(null);
    }
  }, [token, toast, queryClient]);

  if (!isAuthenticated || !user || !isAdmin) {
    return <Navigate to="/profile" replace />;
  }

  const drItems = drData?.items ?? [];

  return (
    <div className="stats-page admin-page">
      <PageMeta
        title="Admin Panel"
        description="Manage users, agents, and properties on Weespas."
      />
      <div className="stats-container">
        <Link to="/profile" className="stats-back">
          <Icon name="arrowLeft" size={18} />
          <span>Back to Profile</span>
        </Link>

        <header className="stats-header">
          <div className="stats-header__top">
            <h1>Admin Panel</h1>
            <span className="stats-header__role">Admin</span>
          </div>
          <p className="stats-header__subtitle">
            User management — search, assign roles, and review deletion requests.
          </p>
        </header>

        <section className="admin-section">
          <div className="admin-section__header">
            <div>
              <h3 className="admin-section__title">User Management</h3>
              <p className="admin-section__hint">
                Search users to assign roles, toggle status, promote agents, or delete accounts.
              </p>
            </div>
            <button
              className="stats-action-btn stats-action-btn--primary"
              onClick={() => setShowSearch(true)}
            >
              <Icon name="search" size={16} />
              Open Search
            </button>
          </div>
          <div className="admin-empty-card">
            <div className="admin-empty-card__icon">
              <Icon name="user" size={36} />
            </div>
            <p className="admin-empty-card__text">
              Use the search panel to find and manage any user, agent, or property.
            </p>
            <ul className="admin-feature-list">
              <li><Icon name="settings" size={14} /> Assign roles (user / agent / staff / admin)</li>
              <li><Icon name="check" size={14} /> Activate or deactivate accounts</li>
              <li><Icon name="verified" size={14} /> Promote users to agents</li>
              <li><Icon name="trash" size={14} /> Permanently delete users</li>
            </ul>
          </div>
        </section>

        {token && <FeaturedManager token={token} />}

        {/* Role Applications — Become Agent / Become Staff queue.
         *
         * Mirrors the deletion-requests block visually so admins have a
         * consistent moderation surface. Two axis of filtering:
         *   - status: pending / approved  (matches deletion-requests tabs)
         *   - role:   all / agent / staff (sub-filter pill row)
         *
         * The "approved" tab also surfaces rejected items because
         * "approved" is the natural terminus of *decided* items; we
         * disambiguate with the `status` chip on each card. (We could
         * add a third tab for rejected-only — left as a follow-up.)
         */}
        <section className="stats-deletion-requests">
          <div className="stats-deletion-requests__header">
            <div className="stats-scope-tabs" role="tablist" aria-label="Role application status">
              <button
                role="tab"
                aria-selected={appStatusFilter === 'pending'}
                className={`stats-scope-tab${appStatusFilter === 'pending' ? ' stats-scope-tab--active' : ''}`}
                onClick={() => setAppStatusFilter('pending')}
              >
                Pending Applications
                {appStatusFilter === 'pending' && (appData?.total ?? 0) > 0 && (
                  <span className="stats-properties__count">{appData?.total}</span>
                )}
              </button>
              <button
                role="tab"
                aria-selected={appStatusFilter === 'approved'}
                className={`stats-scope-tab${appStatusFilter === 'approved' ? ' stats-scope-tab--active' : ''}`}
                onClick={() => setAppStatusFilter('approved')}
              >
                Reviewed Applications
                {appStatusFilter === 'approved' && (appData?.total ?? 0) > 0 && (
                  <span className="stats-properties__count">{appData?.total}</span>
                )}
              </button>
            </div>
            <div className="stats-scope-tabs" role="tablist" aria-label="Role filter">
              {(['all', 'agent', 'staff'] as const).map((r) => (
                <button
                  key={r}
                  role="tab"
                  aria-selected={appRoleFilter === r}
                  className={`stats-scope-tab${appRoleFilter === r ? ' stats-scope-tab--active' : ''}`}
                  onClick={() => setAppRoleFilter(r)}
                >
                  {r === 'all' ? 'All' : r === 'agent' ? 'Agent' : 'Staff'}
                </button>
              ))}
            </div>
          </div>
          {appLoading ? (
            <div className="stats-chart-empty">Loading…</div>
          ) : appError ? (
            <div className="stats-properties-error">
              <Icon name="alertTriangle" size={18} />
              <p>Failed to load role applications.</p>
            </div>
          ) : (appData?.items.length ?? 0) === 0 ? (
            <div className="stats-chart-empty">
              {appStatusFilter === 'pending' ? 'No pending applications' : 'No reviewed applications yet'}
            </div>
          ) : (
            <div className="stats-dr-list">
              {appData!.items.map((app) => (
                <div key={app.id} className="stats-dr-card">
                  <div className="stats-dr-card__info">
                    <p className="stats-dr-card__target">
                      <strong>{app.role_requested === 'agent' ? 'Become an Agent' : 'Become Staff'}</strong>
                      {' · '}
                      {app.applicant_name ?? app.applicant_id}
                    </p>
                    <p className="stats-dr-card__reason">{app.message}</p>
                    <p className="stats-dr-card__date">{formatDate(app.created_at)}</p>
                    {app.status !== 'pending' && app.reviewed_by_name && (
                      <p className="stats-dr-card__requester">
                        Reviewed by: {app.reviewed_by_name}
                      </p>
                    )}
                  </div>
                  {app.status === 'pending' ? (
                    <div className="stats-dr-card__actions">
                      <button
                        className="stats-action-btn stats-action-btn--primary"
                        onClick={() => handleAppDecision(app.id, 'approved')}
                        disabled={processingApp === app.id}
                      >
                        Approve
                      </button>
                      <button
                        className="stats-action-btn stats-action-btn--secondary"
                        onClick={() => handleAppDecision(app.id, 'rejected')}
                        disabled={processingApp === app.id}
                      >
                        Reject
                      </button>
                    </div>
                  ) : (
                    <div className="stats-dr-card__status">
                      <span
                        className={`stats-dr-status stats-dr-status--${app.status === 'approved' ? 'approved' : 'rejected'}`}
                      >
                        {app.status}
                      </span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Deletion Requests — toggle Pending vs Confirmed (approved) */}
        <section className="stats-deletion-requests">
          <div className="stats-deletion-requests__header">
            <div className="stats-scope-tabs" role="tablist" aria-label="Deletion request status">
              <button
                role="tab"
                aria-selected={drFilter === 'pending'}
                className={`stats-scope-tab${drFilter === 'pending' ? ' stats-scope-tab--active' : ''}`}
                onClick={() => setDrFilter('pending')}
              >
                Pending Deletion Requests
                {drFilter === 'pending' && drItems.length > 0 && (
                  <span className="stats-properties__count">{drItems.length}</span>
                )}
              </button>
              <button
                role="tab"
                aria-selected={drFilter === 'approved'}
                className={`stats-scope-tab${drFilter === 'approved' ? ' stats-scope-tab--active' : ''}`}
                onClick={() => setDrFilter('approved')}
              >
                Confirmed Requests
                {drFilter === 'approved' && drItems.length > 0 && (
                  <span className="stats-properties__count">{drItems.length}</span>
                )}
              </button>
            </div>
          </div>
          {drLoading ? (
            <div className="stats-chart-empty">Loading...</div>
          ) : drError ? (
            <div className="stats-properties-error">
              <Icon name="alertTriangle" size={18} />
              <p>Failed to load deletion requests.</p>
            </div>
          ) : drItems.length === 0 ? (
            <div className="stats-chart-empty">
              {drFilter === 'pending' ? 'No pending deletion requests' : 'No confirmed requests yet'}
            </div>
          ) : (
            <div className="stats-dr-list">
              {drItems.map((req) => (
                <div key={req.id} className="stats-dr-card">
                  <div className="stats-dr-card__info">
                    <p className="stats-dr-card__target">Target: {req.target_user_name ?? req.target_user_id ?? 'Deleted user'}</p>
                    <p className="stats-dr-card__requester">Requested by: {req.requested_by_name ?? req.requested_by_id ?? 'Unknown'}</p>
                    <p className="stats-dr-card__reason">{req.reason}</p>
                    <p className="stats-dr-card__date">{formatDate(req.created_at)}</p>
                  </div>
                  {drFilter === 'pending' ? (
                    <div className="stats-dr-card__actions">
                      <button
                        className="stats-action-btn stats-action-btn--primary"
                        onClick={() => handleDrDecision(req.id, 'approved')}
                        disabled={processingDr === req.id}
                      >
                        Approve
                      </button>
                      <button
                        className="stats-action-btn stats-action-btn--secondary"
                        onClick={() => handleDrDecision(req.id, 'rejected')}
                        disabled={processingDr === req.id}
                      >
                        Reject
                      </button>
                    </div>
                  ) : (
                    <div className="stats-dr-card__status">
                      <span className="stats-dr-status stats-dr-status--approved">approved</span>
                    </div>
                  )}
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

export default AdminPage;
