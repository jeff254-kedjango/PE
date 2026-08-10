import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock the api module — the hook talks through it and this keeps the tests network-free. Every
// branch of the discriminated union is a separate mockResolvedValue setup, so we exercise the
// card's kind-branching directly.
vi.mock('../../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../../api/commerce')>('../../../api/commerce');
  return { ...actual, getMyRanking: vi.fn() };
});

import { getMyRanking, type CommerceSession, type RankingResponse } from '../../../api/commerce';
import RankingCard from './RankingCard';

const mockGet = vi.mocked(getMyRanking);
const SESSION: CommerceSession = { token: 'ctok', commerce_url: 'http://c' };

function ranking(overrides: Partial<Extract<RankingResponse, { kind: 'ranking' }>> = {}): RankingResponse {
  return {
    kind: 'ranking',
    rank: 3,
    peer_count: 12,
    radius_km: 10,
    refreshed_at: '2026-08-04T09:00:00Z',
    next_refresh_at: '2026-08-04T09:05:00Z',
    own_score: 0.42,
    weight_breakdown: { sales_score: 0.5, composite_score: 0.35 },
    signals: {
      revenue_cents: 250_000, revenue_window_days: 30,
      rating: 4.6, rating_count: 8,
      follower_count: 21, saves_total: 9,
    },
    ...overrides,
  };
}

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <RankingCard session={SESSION} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('RankingCard — happy path', () => {
  it('renders the caller rank and peer count', async () => {
    mockGet.mockResolvedValue(ranking());
    renderCard();
    // Rank is the visual anchor — it's rendered as "#3".
    expect(await screen.findByText('3')).toBeInTheDocument();
    expect(screen.getByText(/of 12 shops within 10 km/i)).toBeInTheDocument();
  });

  it('formats revenue in KSh (major units)', async () => {
    mockGet.mockResolvedValue(ranking({ signals: { revenue_cents: 123_456, revenue_window_days: 30, rating: 4.0, rating_count: 3, follower_count: 0, saves_total: 0 } }));
    renderCard();
    // 123456 cents = 1234.56 major, rendered as rounded "1235" (no decimal in dashboard tone).
    expect(await screen.findByText('KSh 1235')).toBeInTheDocument();
  });

  it('shows "Unrated" when rating_count is 0 (does NOT show 0.0 stars)', async () => {
    mockGet.mockResolvedValue(ranking({ signals: { revenue_cents: 0, revenue_window_days: 30, rating: 0, rating_count: 0, follower_count: 0, saves_total: 0 } }));
    renderCard();
    expect(await screen.findByText('Unrated')).toBeInTheDocument();
    expect(screen.queryByText(/★ 0.0/)).not.toBeInTheDocument();
  });

  it('says "shop" (singular) when peer_count is 1', async () => {
    mockGet.mockResolvedValue(ranking({ rank: 1, peer_count: 1 }));
    renderCard();
    expect(await screen.findByText(/of 1 shop within 10 km/i)).toBeInTheDocument();
  });
});

describe('RankingCard — paywall', () => {
  it('renders CTAs for one_time_2h and annual when paywall_required', async () => {
    mockGet.mockResolvedValue({
      kind: 'paywall_required',
      reason: 'radius_over_free_cap',
      free_max_radius_km: 200,
      requested_radius_km: 300,
      cta_kinds: ['one_time_2h', 'annual'],
    });
    renderCard();
    expect(await screen.findByText(/Ranking beyond 200 km is a paid feature/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /one-time · 2 hours/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /annual pass/i })).toBeInTheDocument();
  });

  it('shows "coming soon" nudge when a CTA is clicked (payment integration deferred)', async () => {
    mockGet.mockResolvedValue({
      kind: 'paywall_required',
      reason: 'radius_over_free_cap',
      free_max_radius_km: 200,
      requested_radius_km: 300,
      cta_kinds: ['one_time_2h', 'annual'],
    });
    renderCard();
    fireEvent.click(await screen.findByRole('button', { name: /one-time · 2 hours/i }));
    expect(await screen.findByText(/Payments arrive soon/i)).toBeInTheDocument();
  });
});

describe('RankingCard — no shop', () => {
  it('renders the empty hint when the caller has no shop', async () => {
    mockGet.mockResolvedValue({ kind: 'no_shop' });
    renderCard();
    expect(await screen.findByText(/Open a shop to see where you rank/i)).toBeInTheDocument();
    // The rank number MUST NOT render — we don't want a stray "#0" or similar leak. The rank
    // element is the ONLY one carrying the "Rank <n> of <m>" aria-label pattern.
    expect(screen.queryByLabelText(/^Rank \d+ of \d+$/)).not.toBeInTheDocument();
  });
});

describe('RankingCard — radius picker', () => {
  it('refetches when the radius slider moves (new query key = fresh fetch)', async () => {
    mockGet.mockResolvedValue(ranking());
    renderCard();
    // Wait for the initial fetch at 10 km.
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith(SESSION, 10));
    const slider = screen.getByLabelText(/Ranking radius/i);
    fireEvent.change(slider, { target: { value: '50' } });
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith(SESSION, 50));
  });

  it('flags the "· paid" hint when the radius crosses the 200 km line', async () => {
    mockGet.mockResolvedValue(ranking());
    renderCard();
    const slider = screen.getByLabelText(/Ranking radius/i);
    fireEvent.change(slider, { target: { value: '250' } });
    await waitFor(() => expect(screen.getByText(/· paid/i)).toBeInTheDocument());
  });
});

describe('RankingCard — error state', () => {
  it('surfaces the error message when the fetch fails', async () => {
    mockGet.mockRejectedValue(new Error('boom'));
    renderCard();
    // The hook sets retry: 1, so react-query attempts twice before surfacing the error — the
    // findByText default (1s) races the second attempt. A wider timeout lets both settle.
    expect(await screen.findByText(/Couldn.t load your ranking/i, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledTimes(2);
  });
});
