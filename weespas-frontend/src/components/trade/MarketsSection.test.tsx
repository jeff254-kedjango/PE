import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { MarketEntryOut, MarketListOut } from '../../api/commerce';

vi.mock('../../hooks/useMarkets', () => ({
  useMarkets: vi.fn(),
  MARKETS_POLL_MS: 300_000,
}));

import { useMarkets } from '../../hooks/useMarkets';
import MarketsSection from './MarketsSection';

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

const session = { token: 'ctok', commerce_url: 'http://c' } as const;

function renderSection() {
  return render(
    <MemoryRouter initialEntries={['/trade']}>
      <MarketsSection session={session} />
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
  } as ReturnType<typeof useMarkets>);
});

describe('MarketsSection', () => {
  it('caps the grid at 2×3 tiles no matter how many shops are listed', () => {
    mockUseMarkets.mockReturnValue({
      data: market(Array.from({ length: 9 }, (_, i) => entry({ seller_id: `s${i}`, shop_name: `Shop ${i}` }))),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useMarkets>);
    renderSection();
    // The grid is a glance at the strongest six — the rest live on the full /markets board.
    expect(screen.getAllByTestId('markets-tile')).toHaveLength(6);
  });

  it('renders the same data as a market row: name, category, score, change and value', () => {
    renderSection();
    const tile = screen.getByTestId('markets-tile');
    expect(tile).toHaveTextContent('Elite Kicks');
    expect(tile).toHaveTextContent('Shoe Store'); // categoryLabel('shoes')
    expect(tile).toHaveTextContent('83'); // marketScore(0.83)
    expect(tile).toHaveTextContent('KSh 10,971');
    // revenue_trend 1.4 → +40%, in the market's green idiom.
    expect(tile).toHaveTextContent('↑ 40%');
  });

  it('links every tile to its quote page', () => {
    renderSection();
    expect(screen.getByTestId('markets-tile').getAttribute('href')).toBe('/markets/sel-1');
  });

  it('draws a sparkline with an honest accessible label', () => {
    renderSection();
    const spark = screen.getByRole('img', { name: /Elite Kicks: weekly verified revenue over 49 days/i });
    expect(spark).toBeInTheDocument();
  });

  it('header button links to the full board with the arrow-icon + Markets label', () => {
    renderSection();
    const link = screen.getByTestId('markets-section-link');
    expect(link.getAttribute('href')).toBe('/markets');
    expect(link).toHaveTextContent('Markets');
  });

  it('shows an honest empty state when no shop is listed', () => {
    mockUseMarkets.mockReturnValue({
      data: market([]),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useMarkets>);
    renderSection();
    expect(screen.getByTestId('markets-section-empty')).toHaveTextContent(/opt in/i);
    expect(screen.queryAllByTestId('markets-tile')).toHaveLength(0);
  });

  it('shows an error state when the market cannot be read', () => {
    mockUseMarkets.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('boom'),
    } as ReturnType<typeof useMarkets>);
    renderSection();
    expect(screen.getByRole('alert')).toHaveTextContent(/Couldn.t load the market/i);
  });

  it('shimmers a bounded skeleton while the initial fetch is in flight', () => {
    mockUseMarkets.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as ReturnType<typeof useMarkets>);
    renderSection();
    expect(screen.getByTestId('markets-section-skeleton')).toBeInTheDocument();
    expect(screen.queryAllByTestId('markets-tile')).toHaveLength(0);
  });
});
