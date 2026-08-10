import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// Signed-in user so the hooks' `enabled` guards are satisfied.
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ token: 'tok', isAuthenticated: true }),
}));

vi.mock('../../api/notifications', () => ({
  fetchUnreadCount: vi.fn(),
  fetchNotifications: vi.fn(),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
}));

import {
  fetchUnreadCount,
  fetchNotifications,
  markNotificationRead,
} from '../../api/notifications';
import NotificationBell from './NotificationBell';

const mockCount = vi.mocked(fetchUnreadCount);
const mockList = vi.mocked(fetchNotifications);
const mockRead = vi.mocked(markNotificationRead);

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
  mockCount.mockResolvedValue({ count: 0 });
  mockList.mockResolvedValue([]);
  mockRead.mockResolvedValue(undefined);
});
afterEach(() => { vi.clearAllMocks(); });

describe('NotificationBell', () => {
  it('renders the unread badge when there are unread notifications', async () => {
    mockCount.mockResolvedValue({ count: 3 });
    renderBell();
    await waitFor(() => expect(screen.getByText('3')).toBeTruthy());
  });

  it('caps the badge at 9+', async () => {
    mockCount.mockResolvedValue({ count: 42 });
    renderBell();
    await waitFor(() => expect(screen.getByText('9+')).toBeTruthy());
  });

  it('opens the panel and lists notifications, marking one read on click', async () => {
    mockCount.mockResolvedValue({ count: 1 });
    mockList.mockResolvedValue([
      {
        id: 'n1', kind: 'listing_verification', title: 'On the grid',
        body: 'Your listing is monitored', link: '/properties/p1',
        read_at: null, created_at: new Date().toISOString(),
      },
    ]);
    renderBell();

    await waitFor(() => expect(screen.getByText('1')).toBeTruthy());
    fireEvent.click(screen.getByLabelText(/Notifications/));
    await waitFor(() => expect(screen.getByText('On the grid')).toBeTruthy());

    fireEvent.click(screen.getByText('On the grid'));
    await waitFor(() => expect(mockRead).toHaveBeenCalledWith('tok', 'n1'));
  });

  it('shows an empty state when caught up', async () => {
    renderBell();
    fireEvent.click(screen.getByLabelText(/Notifications/));
    await waitFor(() => expect(screen.getByText(/caught up/)).toBeTruthy());
  });
});
