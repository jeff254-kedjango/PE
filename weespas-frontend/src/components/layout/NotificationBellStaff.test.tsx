import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// A STAFF user — the flag-review badge should fold into the bell for this role only.
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ token: 'tok', isAuthenticated: true, user: { id: 'u1', roles: ['staff'] } }),
}));

vi.mock('../../api/notifications', () => ({
  fetchUnreadCount: vi.fn(),
  fetchNotifications: vi.fn(),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
}));

vi.mock('../../api/flagReviews', () => ({
  fetchOpenFlagReviewCount: vi.fn(),
}));

import { fetchUnreadCount, fetchNotifications } from '../../api/notifications';
import { fetchOpenFlagReviewCount } from '../../api/flagReviews';
import NotificationBell from './NotificationBell';

const mockUnread = vi.mocked(fetchUnreadCount);
const mockList = vi.mocked(fetchNotifications);
const mockFlags = vi.mocked(fetchOpenFlagReviewCount);

function renderBell() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <NotificationBell />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockUnread.mockResolvedValue({ count: 0 });
  mockList.mockResolvedValue([]);
  mockFlags.mockResolvedValue({ count: 2 });
});
afterEach(() => { vi.clearAllMocks(); });

describe('NotificationBell (staff flag reviews)', () => {
  it('folds open flag-review count into the badge for staff', async () => {
    mockUnread.mockResolvedValue({ count: 1 });
    mockFlags.mockResolvedValue({ count: 2 });
    renderBell();
    // 1 unread inbox + 2 open flags = 3.
    await waitFor(() => expect(screen.getByText('3')).toBeTruthy());
  });

  it('shows a summarizing flag entry routing to the staff queue', async () => {
    renderBell();
    await waitFor(() => expect(screen.getByText('2')).toBeTruthy());
    fireEvent.click(screen.getByLabelText(/Notifications/));
    await waitFor(() =>
      expect(screen.getByText(/2 flagged buildings to review/)).toBeTruthy(),
    );
  });
});
