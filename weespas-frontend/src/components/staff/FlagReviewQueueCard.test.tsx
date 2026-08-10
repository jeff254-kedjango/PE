import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Staff user so the staff-gated hooks are enabled.
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ token: 'tok', isAuthenticated: true, user: { id: 'u-staff', roles: ['staff'] } }),
}));

vi.mock('../../api/flagReviews', () => ({
  fetchFlagReviews: vi.fn(),
  fetchOpenFlagReviewCount: vi.fn(),
  markFlagReviewSeen: vi.fn(),
  recordFlagReviewView: vi.fn(),
}));

import {
  fetchFlagReviews,
  markFlagReviewSeen,
  recordFlagReviewView,
  type FlagReview,
} from '../../api/flagReviews';
import FlagReviewQueueCard from './FlagReviewQueueCard';

const mockList = vi.mocked(fetchFlagReviews);
const mockSeen = vi.mocked(markFlagReviewSeen);
const mockView = vi.mocked(recordFlagReviewView);

const REVIEW: FlagReview = {
  id: 'r1', flag_id: 'f1', aoi_code: 'kilimani', insar_building_id: 1200045,
  state: 2, source: 'engineer', note: 'Diagonal cracks in ground-floor column',
  observed_at: null, flagged_at: new Date().toISOString(),
  flagged_by_id: 'eng1', flagged_by_name: 'Jane Eng',
  seen: false, seen_at: null, seen_by_id: null, seen_by_name: null, views: 0,
};

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <FlagReviewQueueCard />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockList.mockResolvedValue([REVIEW]);
  mockSeen.mockResolvedValue({ ...REVIEW, seen: true, seen_by_id: 'u-staff', seen_by_name: 'Me', views: 1 });
  mockView.mockResolvedValue({ views: 1 });
});
afterEach(() => { vi.clearAllMocks(); });

describe('FlagReviewQueueCard', () => {
  it('lists an open flag with flagger + building, and records a view on expand', async () => {
    renderCard();
    await waitFor(() => expect(screen.getByText('#1200045')).toBeTruthy());
    expect(screen.getByText(/Jane Eng/)).toBeTruthy();

    // Expanding the row records a distinct view and reveals the note + facts.
    fireEvent.click(screen.getByText('#1200045'));
    await waitFor(() => expect(screen.getByText(/Diagonal cracks/)).toBeTruthy());
    expect(mockView).toHaveBeenCalledWith('tok', 'r1');
  });

  it('marks a review seen via the Mark seen button', async () => {
    renderCard();
    await waitFor(() => expect(screen.getByText('#1200045')).toBeTruthy());
    fireEvent.click(screen.getByText('#1200045'));
    await waitFor(() => expect(screen.getByText('Mark seen')).toBeTruthy());

    fireEvent.click(screen.getByText('Mark seen'));
    await waitFor(() => expect(mockSeen).toHaveBeenCalledWith('tok', 'r1'));
  });

  it('shows the empty state when nothing awaits review', async () => {
    mockList.mockResolvedValue([]);
    renderCard();
    await waitFor(() => expect(screen.getByText(/Nothing awaiting review/)).toBeTruthy());
  });
});
