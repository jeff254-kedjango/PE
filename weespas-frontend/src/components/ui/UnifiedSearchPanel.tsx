import React, { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useDebounce } from '../../hooks/useDebounce';
import { useUnifiedSearch } from '../../hooks/useUnifiedSearch';
import { useToast } from '../../context/ToastContext';
import { deleteAdminUser, updateUserRoles, patchUserStatus, promoteToAgent, submitDeletionRequest } from '../../api/admin';
import { ALL_ROLES, getRoles, hasRole } from '../../utils/roles';
import { resolveMediaUrl } from '../../utils/media';
import Icon from './Icon';
import ConfirmDeleteDialog from './ConfirmDeleteDialog';
import DeletionRequestModal from './DeletionRequestModal';
import type { SearchResultItem, SearchResultCategory } from '../../types/admin';
import type { UserRole } from '../../types/auth';
import './UnifiedSearchPanel.css';

interface UnifiedSearchPanelProps {
  isOpen: boolean;
  onClose: () => void;
  token: string | null;
  userRole: UserRole | undefined;
  currentUserId?: string;
}

type TabFilter = 'all' | SearchResultCategory;

const ALL_TABS: { key: TabFilter; label: string; minRole?: 'staff' }[] = [
  { key: 'all', label: 'All' },
  { key: 'property', label: 'Properties' },
  { key: 'agent', label: 'Agents' },
  { key: 'user', label: 'Users', minRole: 'staff' },
];

const UnifiedSearchPanel: React.FC<UnifiedSearchPanelProps> = ({
  isOpen,
  onClose,
  token,
  userRole,
  currentUserId,
}) => {
  const [query, setQuery] = useState('');
  const [activeTab, setActiveTab] = useState<TabFilter>('all');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [roleMenuId, setRoleMenuId] = useState<string | null>(null);
  const [pendingRoles, setPendingRoles] = useState<UserRole[]>([]);
  const [deletingItem, setDeletingItem] = useState<SearchResultItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [changingRole, setChangingRole] = useState(false);
  const [togglingStatus, setTogglingStatus] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const [requestingDeletion, setRequestingDeletion] = useState<SearchResultItem | null>(null);
  const [submittingDr, setSubmittingDr] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const roleMenuRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const navigate = useNavigate();

  const debouncedQuery = useDebounce(query, 300);
  const { items, isLoading, isError } = useUnifiedSearch(token, debouncedQuery, userRole);

  const filtered = useMemo(() => {
    if (activeTab === 'all') return items;
    return items.filter((item) => item.category === activeTab);
  }, [items, activeTab]);

  const categoryCounts = useMemo(() => {
    const counts: Record<TabFilter, number> = { all: items.length, property: 0, agent: 0, user: 0 };
    for (const item of items) counts[item.category]++;
    return counts;
  }, [items]);

  const grouped = useMemo(() => {
    if (activeTab !== 'all') return null;
    const map = new Map<SearchResultCategory, SearchResultItem[]>();
    for (const item of filtered) {
      const list = map.get(item.category) ?? [];
      list.push(item);
      map.set(item.category, list);
    }
    return map;
  }, [filtered, activeTab]);

  useEffect(() => {
    if (isOpen && inputRef.current) setTimeout(() => inputRef.current?.focus(), 50);
    if (!isOpen) { setQuery(''); setActiveTab('all'); setExpandedId(null); setRoleMenuId(null); }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (roleMenuId) setRoleMenuId(null);
        else if (expandedId) setExpandedId(null);
        else onClose();
      }
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose, roleMenuId, expandedId]);

  useEffect(() => {
    if (isOpen) document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, [isOpen]);

  useEffect(() => {
    if (!roleMenuId) return;
    const handleClick = (e: MouseEvent) => {
      if (roleMenuRef.current && !roleMenuRef.current.contains(e.target as Node)) setRoleMenuId(null);
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [roleMenuId]);

  const getUserId = (item: SearchResultItem): string => {
    if (item.category === 'user') return item.id;
    return item.user_id ?? item.id;
  };

  const handleDeleteConfirm = useCallback(async () => {
    if (!deletingItem || !token) return;
    setDeleting(true);
    try {
      await deleteAdminUser(token, getUserId(deletingItem));
      toast.success(`${deletingItem.name} deleted`);
      queryClient.invalidateQueries({ queryKey: ['unifiedSearch'] });
      setDeletingItem(null);
      setExpandedId(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete');
    } finally {
      setDeleting(false);
    }
  }, [deletingItem, token, toast, queryClient]);

  const openRoleMenu = useCallback((key: string, item: SearchResultItem) => {
    const current = getRoles(item);
    setPendingRoles(current.length > 0 ? current : ['user']);
    setRoleMenuId(key);
  }, []);

  const togglePendingRole = useCallback((role: UserRole) => {
    setPendingRoles((prev) => {
      if (prev.includes(role)) {
        const next: UserRole[] = prev.filter((r) => r !== role);
        return next.length === 0 ? (['user'] as UserRole[]) : next;
      }
      // Selecting any non-user role implies the user is no longer "just user"
      const next: UserRole[] = role === 'user'
        ? (['user'] as UserRole[])
        : [...prev.filter((r) => r !== 'user'), role];
      return next;
    });
  }, []);

  const handleRolesSave = useCallback(
    async (item: SearchResultItem) => {
      if (!token || pendingRoles.length === 0) return;
      setChangingRole(true);
      try {
        await updateUserRoles(token, getUserId(item), pendingRoles);
        toast.success(`${item.name} roles updated: ${pendingRoles.join(', ')}`);
        queryClient.invalidateQueries({ queryKey: ['unifiedSearch'] });
        setRoleMenuId(null);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to update roles');
      } finally {
        setChangingRole(false);
      }
    },
    [token, toast, queryClient, pendingRoles],
  );

  const handleToggleStatus = useCallback(
    async (item: SearchResultItem) => {
      if (!token) return;
      const newActive = item.is_active === false;
      setTogglingStatus(true);
      try {
        await patchUserStatus(token, getUserId(item), newActive);
        toast.success(`${item.name} ${newActive ? 'activated' : 'deactivated'}`);
        queryClient.invalidateQueries({ queryKey: ['unifiedSearch'] });
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to update status');
      } finally {
        setTogglingStatus(false);
      }
    },
    [token, toast, queryClient],
  );

  const handlePromoteToAgent = useCallback(
    async (item: SearchResultItem) => {
      if (!token) return;
      const agentId = window.prompt('Enter the Agent profile ID to link this user to:');
      if (!agentId?.trim()) return;
      setPromoting(true);
      try {
        await promoteToAgent(token, getUserId(item), agentId.trim());
        toast.success(`${item.name} promoted to agent`);
        queryClient.invalidateQueries({ queryKey: ['unifiedSearch'] });
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to promote user');
      } finally {
        setPromoting(false);
      }
    },
    [token, toast, queryClient],
  );

  const handleViewProfile = useCallback(
    (item: SearchResultItem) => {
      onClose();
      if (item.category === 'agent') navigate(`/agents/${item.id}`);
      else if (item.agent_id) navigate(`/agents/${item.agent_id}`);
    },
    [onClose, navigate],
  );

  const handleSubmitDeletionRequest = useCallback(
    async (reason: string) => {
      if (!requestingDeletion || !token) return;
      setSubmittingDr(true);
      try {
        await submitDeletionRequest(token, getUserId(requestingDeletion), reason);
        toast.success(`Deletion request submitted for ${requestingDeletion.name}`);
        queryClient.invalidateQueries({ queryKey: ['staffDeletionRequests'] });
        setRequestingDeletion(null);
        setExpandedId(null);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to submit deletion request');
      } finally {
        setSubmittingDr(false);
      }
    },
    [requestingDeletion, token, toast, queryClient],
  );

  const isAdmin = userRole === 'admin';
  const isStaffOrAdmin = userRole === 'staff' || userRole === 'admin';
  const hasQuery = debouncedQuery.trim().length >= 2;

  const visibleTabs = useMemo(() =>
    ALL_TABS.filter((tab) => !tab.minRole || isStaffOrAdmin),
    [isStaffOrAdmin],
  );

  const categoryLabel = (cat: SearchResultCategory) => {
    if (cat === 'user') return 'Users';
    if (cat === 'agent') return 'Agents';
    return 'Properties';
  };

  const renderResult = (item: SearchResultItem) => {
    const key = `${item.category}-${item.id}`;
    const isExpanded = expandedId === key;
    const isPerson = item.category !== 'property';
    const hasAgentProfile = item.category === 'agent' || (item.category === 'user' && !!item.agent_id);

    return (
      <div key={key} className={`search-result${isExpanded ? ' search-result--expanded' : ''}`}>
        <div
          className={`search-result__row${isPerson ? ' search-result__row--clickable' : ''}`}
          onClick={isPerson ? () => setExpandedId(isExpanded ? null : key) : undefined}
          role={isPerson ? 'button' : undefined}
          tabIndex={isPerson ? 0 : undefined}
          onKeyDown={isPerson ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpandedId(isExpanded ? null : key); } } : undefined}
        >
          {item.avatar ? (
            // Route through resolveMediaUrl so root-relative /uploads/avatars/*
            // paths hit the backend origin (the StaticFiles mount), not the SPA.
            <img src={resolveMediaUrl(item.avatar)} alt={item.name} className="search-result__avatar" loading="lazy" />
          ) : (
            <div className="search-result__avatar-placeholder">
              <Icon name={isPerson ? 'user' : 'grid'} size={18} />
            </div>
          )}

          <div className="search-result__info">
            <p className="search-result__name">{item.name}</p>
            <p className="search-result__subtitle">{item.subtitle}</p>
            {(item.role || (item.roles && item.roles.length > 0)) && (
              <div className="search-result__badges">
                {getRoles(item).map((r) => (
                  <span
                    key={r}
                    className={`search-result__role-badge search-result__role-badge--${r}`}
                  >
                    {r}
                  </span>
                ))}
                {item.is_active === false && (
                  <span className="search-result__inactive-badge">Inactive</span>
                )}
              </div>
            )}
          </div>

          {isPerson && (
            <span className={`search-result__chevron${isExpanded ? ' search-result__chevron--open' : ''}`}>
              <Icon name="chevronRight" size={16} />
            </span>
          )}
        </div>

        {isExpanded && isPerson && (
          <div className="search-result__action-bar">
            {isAdmin && (
              <div className="search-result__role-dropdown" ref={roleMenuId === key ? roleMenuRef : undefined}>
                <button
                  className="search-result__action-pill"
                  onClick={() => (roleMenuId === key ? setRoleMenuId(null) : openRoleMenu(key, item))}
                  disabled={changingRole}
                >
                  <Icon name="settings" size={14} />
                  Assign Permissions
                </button>
                {roleMenuId === key && (
                  <div className="search-result__role-menu search-result__role-menu--checkbox">
                    {ALL_ROLES.map((role) => {
                      const checked = pendingRoles.includes(role);
                      return (
                        <label
                          key={role}
                          className={`search-result__role-option${checked ? ' search-result__role-option--current' : ''}`}
                        >
                          <input
                            type="checkbox"
                            className="search-result__role-checkbox"
                            checked={checked}
                            onChange={() => togglePendingRole(role)}
                            disabled={changingRole}
                          />
                          <span>{role}</span>
                        </label>
                      );
                    })}
                    <div className="search-result__role-menu-actions">
                      <button
                        className="search-result__action-pill"
                        onClick={() => setRoleMenuId(null)}
                        disabled={changingRole}
                      >
                        Cancel
                      </button>
                      <button
                        className="search-result__action-pill search-result__action-pill--success"
                        onClick={() => handleRolesSave(item)}
                        disabled={changingRole || pendingRoles.length === 0}
                      >
                        <Icon name="check" size={12} />
                        Save
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {isAdmin && isPerson && (
              <button
                className={`search-result__action-pill ${
                  item.is_active === false
                    ? 'search-result__action-pill--success'
                    : 'search-result__action-pill--warning'
                }`}
                onClick={() => handleToggleStatus(item)}
                disabled={togglingStatus || getUserId(item) === currentUserId}
                title={getUserId(item) === currentUserId ? 'Cannot change your own status' : undefined}
              >
                <Icon name={item.is_active === false ? 'check' : 'x'} size={14} />
                {item.is_active === false ? 'Activate' : 'Deactivate'}
              </button>
            )}

            {isAdmin && item.category === 'user' && !hasRole(item, 'agent') && !item.agent_id && (
              <button
                className="search-result__action-pill search-result__action-pill--promote"
                onClick={() => handlePromoteToAgent(item)}
                disabled={promoting}
              >
                <Icon name="verified" size={14} />
                Promote to Agent
              </button>
            )}

            {isAdmin && (
              <button
                className="search-result__action-pill search-result__action-pill--danger"
                onClick={() => setDeletingItem(item)}
              >
                <Icon name="trash" size={14} />
                Delete
              </button>
            )}

            {isStaffOrAdmin && isPerson && !hasRole(item, 'admin') && (
              <button
                className="search-result__action-pill search-result__action-pill--warning"
                onClick={() => setRequestingDeletion(item)}
              >
                <Icon name="alertTriangle" size={14} />
                Request Deletion
              </button>
            )}

            {hasAgentProfile && (
              <button
                className="search-result__action-pill search-result__action-pill--profile"
                onClick={() => handleViewProfile(item)}
              >
                <Icon name="eye" size={14} />
                View Profile
              </button>
            )}
          </div>
        )}
      </div>
    );
  };

  if (!isOpen) return null;

  return createPortal(
    <>
      <div className="unified-search-panel-overlay" onClick={onClose} />
      <div className="unified-search-panel" role="dialog" aria-label="Search">
        <div className="unified-search-panel__handle"><span /></div>
        <div className="unified-search-panel__header">
          <h2 className="unified-search-panel__title">Search</h2>
          <button className="unified-search-panel__close" onClick={onClose} aria-label="Close search">
            <Icon name="x" size={20} />
          </button>
        </div>

        <div className="unified-search-panel__input-wrapper">
          <span className="unified-search-panel__input-icon">
            <Icon name="search" size={16} />
          </span>
          <input
            ref={inputRef}
            type="text"
            className="unified-search-panel__input"
            placeholder="Search users, agents, properties..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search users, agents, and properties"
          />
        </div>

        {hasQuery && (
          <div className="unified-search-panel__tabs">
            {visibleTabs.map((tab) => (
              <button
                key={tab.key}
                className={`unified-search-panel__tab${activeTab === tab.key ? ' unified-search-panel__tab--active' : ''}`}
                onClick={() => setActiveTab(tab.key)}
              >
                {tab.label}
                {categoryCounts[tab.key] > 0 && (
                  <span className="unified-search-panel__tab-count">{categoryCounts[tab.key]}</span>
                )}
              </button>
            ))}
          </div>
        )}

        <div className="unified-search-panel__results">
          {!hasQuery && (
            <div className="unified-search-panel__empty">
              <div className="unified-search-panel__empty-icon"><Icon name="search" size={40} /></div>
              <p className="unified-search-panel__empty-text">Search across the platform</p>
              <p className="unified-search-panel__empty-hint">Find properties, agents, and users by name, email, or phone</p>
            </div>
          )}

          {hasQuery && isLoading && (
            <div className="unified-search-panel__loading">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="unified-search-panel__skeleton">
                  <div className="unified-search-panel__skeleton-avatar" />
                  <div className="unified-search-panel__skeleton-lines">
                    <div className="unified-search-panel__skeleton-line" />
                    <div className="unified-search-panel__skeleton-line" />
                  </div>
                </div>
              ))}
            </div>
          )}

          {hasQuery && isError && (
            <div className="unified-search-panel__error">
              <Icon name="alertTriangle" size={16} />
              <span>Failed to load results. Try again.</span>
            </div>
          )}

          {hasQuery && !isLoading && !isError && filtered.length === 0 && (
            <div className="unified-search-panel__empty">
              <div className="unified-search-panel__empty-icon"><Icon name="search" size={36} /></div>
              <p className="unified-search-panel__empty-text">No results for &ldquo;{debouncedQuery}&rdquo;</p>
            </div>
          )}

          {hasQuery && !isLoading && filtered.length > 0 && (
            <>
              {activeTab === 'all' && grouped
                ? Array.from(grouped.entries()).map(([category, categoryItems]) => (
                    <div key={category}>
                      <h4 className="unified-search-panel__category-label">{categoryLabel(category)}</h4>
                      {categoryItems.map(renderResult)}
                    </div>
                  ))
                : filtered.map(renderResult)}
            </>
          )}
        </div>
      </div>

      <ConfirmDeleteDialog
        isOpen={!!deletingItem}
        onClose={() => setDeletingItem(null)}
        onConfirm={handleDeleteConfirm}
        title="Delete User"
        message={`Are you sure you want to permanently delete "${deletingItem?.name}"? This action cannot be undone.`}
        isLoading={deleting}
      />

      <DeletionRequestModal
        isOpen={!!requestingDeletion}
        onClose={() => setRequestingDeletion(null)}
        onSubmit={handleSubmitDeletionRequest}
        targetName={requestingDeletion?.name ?? ''}
        isLoading={submittingDr}
      />
    </>,
    document.body,
  );
};

export default UnifiedSearchPanel;
