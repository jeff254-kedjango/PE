import React, { useEffect, useState } from 'react';
import Icon from '../ui/Icon';
import { useDebounce } from '../../hooks/useDebounce';
import {
  useStaffDirectory,
  type DirectoryItem,
  type DirectoryMode,
  DIRECTORY_PAGE_SIZE,
} from '../../hooks/useStaffDirectory';
import { formatLastSeen } from '../../utils/format';
import { resolveMediaUrl } from '../../utils/media';
import './StaffDirectoryCard.css';

interface Props {
  token: string | null;
  onOpenSearch?: () => void;
}

const TABS: { key: DirectoryMode; label: string }[] = [
  { key: 'users', label: 'Users' },
  { key: 'agents', label: 'Agents' },
  { key: 'staff', label: 'Staff' },
];

const ROLE_LABEL: Record<string, string> = {
  admin: 'Admin',
  staff: 'Staff',
  agent: 'Agent',
  user: 'User',
};

function crossRoleTags(item: DirectoryItem, mode: DirectoryMode): string[] {
  const roles = new Set(item.roles ?? []);
  const tags: string[] = [];
  const considerAgent =
    mode !== 'agents' && (roles.has('agent') || Boolean(item.agent_id));
  const considerStaff = mode !== 'staff' && roles.has('staff');
  const considerAdmin = roles.has('admin');
  const considerUser =
    mode === 'agents' && !roles.has('agent') ? false : mode !== 'users' && roles.has('user');

  if (considerAgent) tags.push(ROLE_LABEL.agent);
  if (considerStaff) tags.push(ROLE_LABEL.staff);
  if (considerAdmin) tags.push(ROLE_LABEL.admin);
  if (considerUser) tags.push(ROLE_LABEL.user);
  return tags;
}

const StaffDirectoryCard: React.FC<Props> = ({ token, onOpenSearch }) => {
  const [mode, setMode] = useState<DirectoryMode>('users');
  const [query, setQuery] = useState('');
  const debouncedQuery = useDebounce(query, 300);
  // Independent page per tab — switching tabs preserves each tab's page.
  const [pages, setPages] = useState<Record<DirectoryMode, number>>({
    users: 0,
    agents: 0,
    staff: 0,
  });
  const page = pages[mode];
  const setPage = (next: number) =>
    setPages((prev) => ({ ...prev, [mode]: Math.max(0, next) }));

  // Reset only the current tab's page when its query changes.
  useEffect(() => {
    setPages((prev) => ({ ...prev, [mode]: 0 }));
  }, [debouncedQuery, mode]);

  const { items, total, isLoading, isFetching, error } = useStaffDirectory(
    token,
    mode,
    debouncedQuery,
    page,
  );

  const pageCount = Math.max(1, Math.ceil(total / DIRECTORY_PAGE_SIZE));
  const rangeStart = total === 0 ? 0 : page * DIRECTORY_PAGE_SIZE + 1;
  const rangeEnd = Math.min(total, page * DIRECTORY_PAGE_SIZE + items.length);

  const handleChat = (item: DirectoryItem) => {
    // Messaging integration coming later — keep the button visible & wired.
    // eslint-disable-next-line no-console
    console.debug('[chat] target:', item.id, item.name);
  };

  return (
    <section className="admin-section staff-directory">
      <div className="admin-section__header">
        <div>
          <h3 className="admin-section__title">User & Agent Search</h3>
          <p className="admin-section__hint">
            Browse platform members. Toggle between Users, Agents, and Staff.
          </p>
        </div>
        {onOpenSearch && (
          <button
            className="stats-action-btn stats-action-btn--primary"
            onClick={onOpenSearch}
          >
            <Icon name="search" size={16} />
            Open Search
          </button>
        )}
      </div>

      <div className="staff-directory__toolbar">
        <div className="stats-scope-tabs staff-directory__tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={mode === t.key}
              className={`stats-scope-tab${mode === t.key ? ' stats-scope-tab--active' : ''}`}
              onClick={() => setMode(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="staff-directory__search">
          <Icon name="search" size={16} />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={`Search ${mode}…`}
            aria-label={`Search ${mode}`}
          />
        </div>
      </div>

      {isLoading ? (
        <div className="staff-directory__state">Loading {mode}…</div>
      ) : error ? (
        <div className="staff-directory__state staff-directory__state--error">
          <Icon name="alertTriangle" size={16} /> Failed to load {mode}.
        </div>
      ) : items.length === 0 ? (
        <div className="staff-directory__state">
          {debouncedQuery
            ? `No ${mode} match "${debouncedQuery}".`
            : `No ${mode} on the platform yet.`}
        </div>
      ) : (
        <>
          <div className="staff-directory__count">
            {total > 0
              ? `Showing ${rangeStart}–${rangeEnd} of ${total} ${mode}`
              : `Showing ${items.length} ${mode}`}
          </div>
          <ul
            className={`staff-directory__list${isFetching ? ' staff-directory__list--loading' : ''}`}
          >
            {items.map((item) => {
              const presence = formatLastSeen(item.last_seen_at);
              const tags = crossRoleTags(item, mode);
              return (
                <li key={`${item.source}-${item.id}`} className="staff-directory__row">
                  <div className="staff-directory__avatar-wrap">
                    {item.avatar ? (
                      <img
                        // Backend serves /uploads/* on its own origin; the
                        // helper prepends that origin so the request lands
                        // on the StaticFiles mount instead of the SPA host.
                        src={resolveMediaUrl(item.avatar)}
                        alt=""
                        className="staff-directory__avatar"
                      />
                    ) : (
                      <div className="staff-directory__avatar staff-directory__avatar--placeholder">
                        <Icon name="user" size={20} />
                      </div>
                    )}
                    {item.is_online && (
                      <span
                        className="staff-directory__online-dot"
                        aria-label="Online"
                        title="Online now"
                      />
                    )}
                  </div>

                  <div className="staff-directory__body">
                    <div className="staff-directory__name-row">
                      <span className="staff-directory__name">{item.name}</span>
                      {tags.map((tag) => (
                        <span key={tag} className="staff-directory__cross-role">
                          {tag}
                        </span>
                      ))}
                    </div>
                    <div className="staff-directory__subtitle">{item.subtitle}</div>
                    <div
                      className={`staff-directory__last-active${
                        presence.isOnline ? ' staff-directory__last-active--online' : ''
                      }`}
                    >
                      {presence.label}
                    </div>
                  </div>

                  <button
                    type="button"
                    className="staff-directory__chat-btn"
                    onClick={() => handleChat(item)}
                    aria-label={`Message ${item.name}`}
                    title="Messaging coming soon"
                  >
                    <Icon name="chat" size={18} />
                  </button>
                </li>
              );
            })}
          </ul>

          {pageCount > 1 && (
            <nav
              className="staff-directory__pagination"
              aria-label={`${mode} pagination`}
            >
              <button
                type="button"
                className="staff-directory__page-btn"
                onClick={() => setPage(page - 1)}
                disabled={page === 0 || isFetching}
              >
                <Icon name="chevronLeft" size={16} />
                <span>Prev</span>
              </button>
              <span className="staff-directory__page-indicator">
                Page {page + 1} of {pageCount}
              </span>
              <button
                type="button"
                className="staff-directory__page-btn"
                onClick={() => setPage(page + 1)}
                disabled={page + 1 >= pageCount || isFetching}
              >
                <span>Next</span>
                <Icon name="chevronRight" size={16} />
              </button>
            </nav>
          )}
        </>
      )}
    </section>
  );
};

export default StaffDirectoryCard;
