import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { MarketEntryOut, MarketListOut } from '../api/commerce';

vi.mock('../context/AuthContext', () => ({
  useAuth: vi.fn(() => ({ isAuthenticated: true, token: 't', user: { name: 'X' } })),
}));
vi.mock('../hooks/useCommerceSession', () => ({
  useCommerceSession: () => ({ session: { token: 'ctok', commerce_url: 'http://c' }, isLoading: false, error: null }),
}));
vi.mock('../hooks/useMarkets', () => ({
  useMarkets: vi.fn(),
  MARKETS_POLL_MS: 300_000,
}));

import { useMarkets } from '../hooks/useMarkets';
import { useAuth } from '../context/AuthContext';
import MarketsPage from './MarketsPage';

const mockUseMarkets = vi.mocked(useMarkets);

function entry(overrides: Partial<MarketEntryOut> = {}): MarketEntryOut {
  return {
    seller_id: 'sel-1',
    seller_name: 'Kwemange Nyagrowa',
    shop_name: 'Elite Kicks',
    category: 'shoes',
    score: 0.83,
    is_scoreable: true,
    currency: 'KES',
    revenue_cents: 1_097_070,
    revenue_trend: 1.4,
    rating: 4.6,
    rating_count: 8,
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

function market(entries: MarketEntryOut[]): MarketListOut {
  return { entries, window_days: 90, revenue_saturation_cents: 150_000 };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/markets']}>
      <MarketsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockUseMarkets.mockReset();
  mockUseMarkets.mockReturnValue({
    data: market([entry()]),
    isLoading: false,
    isError: false,
    error: null,
    // The page only reads the fields above; silence the rest of the query object.
  } as ReturnType<typeof useMarkets>);
});

describe('MarketsPage', () => {
  it('renders a breadcrumb back to /trade', async () => {
    renderPage();
    const crumb = screen.getByTestId('markets-breadcrumb');
    expect(within(crumb).getByRole('link', { name: 'Trade' })).toHaveAttribute('href', '/trade');
    expect(within(crumb).getByText('WeesStock Markets')).toBeInTheDocument();
  });

  it('renders the regulatory boundary on the surface itself', async () => {
    renderPage();
    // Discovery/analytics only — the label that keeps this surface honest is part of the page,
    // not buried in a footer link.
    expect(screen.getByTestId('markets-regulatory-chip')).toHaveTextContent(/not a securities market/i);
  });

  it('renders each shop as a ticker row with value, change and score', async () => {
    mockUseMarkets.mockReturnValue({
      data: market([entry(), entry({ seller_id: 'sel-2', shop_name: 'Axlesia', score: 0.8, revenue_cents: 459_925, revenue_trend: 0.9 })]),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useMarkets>);
    renderPage();

    const rows = screen.getAllByTestId('market-row');
    expect(rows).toHaveLength(2);

    const first = within(rows[0]);
    expect(first.getByText('Elite Kicks')).toBeInTheDocument();
    expect(first.getByText('Score 83')).toBeInTheDocument();
    expect(first.getByText('KSh 10,971')).toBeInTheDocument();
    // revenue_trend 1.4 → +40%, drawn up in the market's green idiom.
    expect(first.getByTestId('market-change')).toHaveTextContent('↑ 40%');

    const second = within(rows[1]);
    // revenue_trend 0.9 → −10%, drawn down in red.
    expect(second.getByTestId('market-change')).toHaveTextContent('↓ 10%');
  });

  it('links every row to its detail page', async () => {
    renderPage();
    const row = screen.getByTestId('market-row');
    expect(row.getAttribute('href')).toBe('/markets/sel-1');
  });

  it('renders the top strip from the strongest entries', async () => {
    mockUseMarkets.mockReturnValue({
      data: market([
        entry({ seller_id: 's1', shop_name: 'One', score: 0.9 }),
        entry({ seller_id: 's2', shop_name: 'Two', score: 0.8 }),
        entry({ seller_id: 's3', shop_name: 'Three', score: 0.7 }),
        entry({ seller_id: 's4', shop_name: 'Four', score: 0.6 }),
      ]),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useMarkets>);
    renderPage();
    // The strip is a glance at the strongest few — bounded, never the whole board.
    expect(screen.getAllByTestId('market-top-chip')).toHaveLength(3);
  });

  it('draws a sparkline with an honest accessible label per row', async () => {
    renderPage();
    const spark = screen.getByRole('img', { name: /Elite Kicks: weekly verified revenue over 49 days/i });
    expect(spark).toBeInTheDocument();
  });

  it('shows a plain empty state when no shop is listed', async () => {
    mockUseMarkets.mockReturnValue({
      data: market([]),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useMarkets>);
    renderPage();
    expect(screen.getByTestId('markets-empty')).toHaveTextContent(/opt in/i);
    expect(screen.queryAllByTestId('market-row')).toHaveLength(0);
  });

  it('shows an error state when the market cannot be read', async () => {
    mockUseMarkets.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('boom'),
    } as ReturnType<typeof useMarkets>);
    renderPage();
    expect(screen.getByRole('alert')).toHaveTextContent(/Couldn.t load the market/i);
  });

  it('gates on auth', async () => {
    vi.mocked(useAuth).mockReturnValue({ isAuthenticated: false, token: null, user: null } as never);
    renderPage();
    expect(screen.getByRole('link', { name: /sign in/i })).toHaveAttribute('href', '/login?next=markets');
  });
});
