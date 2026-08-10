// Notification bell + dropdown inbox for the navbar.
//
// Shows an unread badge (polled every 60s by useUnreadNotificationCount). Clicking
// opens a dropdown that lazily fetches the inbox list. Clicking an item marks it read
// and navigates to its deep-link. Authenticated-only — the navbar renders this just
// for signed-in users, so the hooks' `enabled` guards never fire for anonymous users.
import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Icon from '../ui/Icon';
import { formatDate } from '../../utils/format';
import {
  useUnreadNotificationCount,
  useNotificationList,
  useMarkNotificationRead,
  useMarkAllNotificationsRead,
} from '../../hooks/useNotifications';
import { useOpenFlagReviewCount } from '../../hooks/useFlagReviews';
import { useAuth } from '../../context/AuthContext';
import { isStaffOrAdmin } from '../../utils/roles';
import type { AppNotification } from '../../api/notifications';
import './NotificationBell.css';

const NotificationBell: React.FC = () => {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const { user } = useAuth();
  const isStaff = isStaffOrAdmin(user);

  const { data: unread } = useUnreadNotificationCount();
  const { data: items, isLoading } = useNotificationList(open);
  const markRead = useMarkNotificationRead();
  const markAll = useMarkAllNotificationsRead();
  // Staff/admin also see open flagged-building reviews; the hook self-disables for
  // everyone else, so `openFlags` is always 0 for a normal user.
  const { data: flagCount } = useOpenFlagReviewCount();
  const openFlags = isStaff ? (flagCount?.count ?? 0) : 0;

  const unreadCount = (unread?.count ?? 0) + openFlags;

  // Close on outside-click / Escape (mirrors the navbar's menu behavior).
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const onItemClick = (n: AppNotification) => {
    if (!n.read_at) markRead.mutate(n.id);
    setOpen(false);
    if (n.link) navigate(n.link);
  };

  return (
    <div className="notif" ref={wrapRef}>
      <button
        type="button"
        className="navbar__icon-btn notif__trigger"
        aria-label={unreadCount > 0 ? `Notifications (${unreadCount} unread)` : 'Notifications'}
        aria-haspopup="true"
        aria-expanded={open}
        title="Notifications"
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name="bell" size={20} />
        {unreadCount > 0 && (
          <span className="navbar__badge notif__badge" aria-hidden="true">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="notif__panel" role="dialog" aria-label="Notifications">
          <div className="notif__head">
            <span className="notif__title">Notifications</span>
            {unreadCount > 0 && (
              <button
                type="button"
                className="notif__mark-all"
                onClick={() => markAll.mutate()}
                disabled={markAll.isPending}
              >
                Mark all read
              </button>
            )}
          </div>

          <div className="notif__list">
            {/* Staff/admin: a single summarizing entry for open flagged-building
                reviews. The full record (sent by / note / seen by / views) lives on
                the staff queue, so this just routes there. */}
            {openFlags > 0 && (
              <button
                type="button"
                className="notif__item notif__item--unread"
                onClick={() => { setOpen(false); navigate('/staff'); }}
              >
                <span className="notif__dot" aria-hidden="true" />
                <span className="notif__item-body">
                  <span className="notif__item-title">
                    {openFlags} flagged building{openFlags > 1 ? 's' : ''} to review
                  </span>
                  <span className="notif__item-text">
                    A certifier flagged a building. Tap to open the review queue.
                  </span>
                </span>
              </button>
            )}
            {isLoading ? (
              <div className="notif__empty">Loading…</div>
            ) : (!items || items.length === 0) && openFlags === 0 ? (
              <div className="notif__empty">You're all caught up ✨</div>
            ) : (
              (items ?? []).map((n) => (
                <button
                  key={n.id}
                  type="button"
                  className={`notif__item${n.read_at ? '' : ' notif__item--unread'}`}
                  onClick={() => onItemClick(n)}
                >
                  {!n.read_at && <span className="notif__dot" aria-hidden="true" />}
                  <span className="notif__item-body">
                    <span className="notif__item-title">{n.title}</span>
                    <span className="notif__item-text">{n.body}</span>
                    <span className="notif__item-time">{formatDate(n.created_at ?? undefined)}</span>
                  </span>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default NotificationBell;
