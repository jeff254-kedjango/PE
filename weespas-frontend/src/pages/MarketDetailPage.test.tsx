import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { MarketDetailOut } from '../api/commerce';

vi.mock('../context/AuthContext', () => ({
  useAuth: vi.fn(() => ({ isAuthenticated: true, token: 't', user: { name: 'X' } })),
}));
vi.mock('../hooks/useCommerceSession', () => ({
  useCommerceSession: () => ({ session: { token: 'ctok', commerce_url: 'http://c' }, isLoading: false, error: null }),
}));
vi.mock('../hooks/useMarketDetail', () => ({
  useMarketDetail: vi.fn(),
}));

import { useMarketDetail } from '../hooks/useMarketDetail';
import { useAuth } from '../context/AuthContext';
import MarketDetailPage from './MarketDetailPage';

const mockDetail = vi.mocked(useMarketDetail);

function detail(overrides: Partial<MarketDetailOut> = {}): MarketDetailOut {
  return {
    seller: { seller_id: 'sel-1', seller_name: 'Kwemange Nyagrowa', shop_name: 'Elite Kicks', category: 'shoes' },
    profile: {
      score: 0.83,
      is_scoreable: true,
      missing_for_score: [],
      orders_needed: 0,
      days_needed: 0,
      currency: 'KES',
      revenue_cents: 1_097_070,
      recent_revenue_cents: 500_000,
      avg_order_value_cents: 23_600,
      revenue_trend: 1.4,
      settled_orders: 46,
      failed_orders: 3,
      fulfilment_rate: 0.9388,
      unique_buyers: 21,
      repeat_buyers: 9,
      repeat_rate: 0.429,
      rating: 4.6,
      rating_count: 8,
      inquiries: 12,
      tenure_days: 400,
      components: [
        { key: 'revenue', label: 'Verified revenue', weighted: 0.4, weight: 0.4 },
        { key: 'fulfilment', label: 'Fulfilment rate', weighted: 0.25, weight: 0.25 },
        { key: 'repeat', label: 'Repeat buyers', weighted: 0.06, weight: 0.15 },
        { key: 'rating', label: 'Buyer rating', weighted: 0.1, weight: 0.12 },
        { key: 'tenure', label: 'Trading history', weighted: 0.02, weight: 0.08 },
      ],
      window_days: 90,
      recent_window_days: 30,
    },
    series: {
      series_cents: [10_000, 20_000, 5_000, 30_000, 40_000, 25_000, 60_000],
      bucket_days: 7,
      bucket_count: 7,
      window_days: 49,
      currency: 'KES',
    },
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/markets/sel-1']}>
      <Routes>
        <Route path="/markets/:sellerId" element={<MarketDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockDetail.mockReset();
  mockDetail.mockReturnValue({
    data: detail(),
    isLoading: false,
    isError: false,
    error: null,
  } as ReturnType<typeof useMarketDetail>);
});

describe('MarketDetailPage', () => {
  it('renders the quote header: name, score, momentum and owner', async () => {
    renderPage();
    expect(screen.getByTestId('market-detail-name')).toHaveTextContent('Elite Kicks');
    expect(screen.getByTestId('market-detail-score')).toHaveTextContent('83');
    expect(screen.getByTestId('market-detail-trend')).toHaveTextContent('↑ 40%');
    expect(screen.getByText(/Owned by Kwemange Nyagrowa/)).toBeInTheDocument();
  });

  it('renders the revenue chart with its honest label', async () => {
    renderPage();
    expect(screen.getByText(/Weekly verified revenue · last 49 days/i)).toBeInTheDocument();
    expect(
      screen.getByRole('img', { name: /Elite Kicks: weekly verified revenue over 49 days/i }),
    ).toBeInTheDocument();
  });

  it('renders the full score breakdown (doctrine 1)', async () => {
    renderPage();
    expect(screen.getAllByTestId('market-detail-component')).toHaveLength(5);
    expect(screen.getByText('Verified revenue')).toBeInTheDocument();
  });

  it('renders the facts grid with the same money the seller sees', async () => {
    renderPage();
    // The total appears twice — chart stats and facts grid — and MUST agree: the page is one
    // number everywhere, never a chart that quotes different money than the facts.
    expect(screen.getAllByText('KSh 10,971')).toHaveLength(2);
    expect(screen.getByText('★ 4.6 (8)')).toBeInTheDocument();
  });

  it('carries the regulatory boundary and links into the real shop', async () => {
    renderPage();
    expect(screen.getByTestId('market-detail-regulatory')).toHaveTextContent(/not a securities market/i);
    expect(screen.getByRole('link', { name: /visit shop/i })).toHaveAttribute('href', '/shop/sel-1');
  });

  it('links back to the market list', async () => {
    renderPage();
    expect(screen.getByTestId('market-detail-back')).toHaveAttribute('href', '/markets');
  });

  it('shows a growth prompt instead of a score on a thin file', async () => {
    mockDetail.mockReturnValue({
      data: detail({
        profile: {
          ...detail().profile,
          score: null,
          is_scoreable: false,
          orders_needed: 4,
          days_needed: 12,
          revenue_trend: null,
        },
      }),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useMarketDetail>);
    renderPage();
    const pending = screen.getByTestId('market-detail-pending');
    expect(pending).toHaveTextContent(/4 more completed sales/i);
    expect(screen.queryByTestId('market-detail-score')).not.toBeInTheDocument();
  });

  it('renders a neutral message for an unlisted or unknown shop', async () => {
    mockDetail.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('404'),
    } as ReturnType<typeof useMarketDetail>);
    renderPage();
    const missing = screen.getByTestId('market-detail-missing');
    // Uniform copy: an unlisted shop is indistinguishable from a garbage id, and the page
    // must never hint which it was.
    expect(within(missing).getByText(/isn.t on WeesStock Markets/i)).toBeInTheDocument();
  });

  it('gates on auth', async () => {
    vi.mocked(useAuth).mockReturnValue({ isAuthenticated: false, token: null, user: null } as never);
    renderPage();
    expect(screen.getByRole('link', { name: /sign in/i })).toHaveAttribute('href', '/login?next=markets/sel-1');
  });
});
