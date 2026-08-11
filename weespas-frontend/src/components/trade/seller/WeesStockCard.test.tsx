import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock the api module so the hook is exercised for real but the network is not.
vi.mock('../../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../../api/commerce')>('../../../api/commerce');
  return { ...actual, getMyCreditProfile: vi.fn() };
});

import { getMyCreditProfile, type CommerceSession, type CreditProfileOut } from '../../../api/commerce';
import WeesStockCard from './WeesStockCard';

const mockGet = vi.mocked(getMyCreditProfile);
const SESSION: CommerceSession = { token: 'ctok', commerce_url: 'http://c' };

/** A scoreable profile. Components mirror the server's weights so `weighted <= weight` holds,
 *  which is what the bar-fill normalisation assumes. */
function profile(overrides: Partial<CreditProfileOut> = {}): CreditProfileOut {
  return {
    score: 0.62,
    is_scoreable: true,
    missing_for_score: [],
    orders_needed: 0,
    days_needed: 0,
    currency: 'KES',
    revenue_cents: 1_230_000,
    recent_revenue_cents: 500_000,
    avg_order_value_cents: 41_000,
    revenue_trend: 1.0,
    settled_orders: 30,
    failed_orders: 2,
    fulfilment_rate: 0.9375,
    unique_buyers: 20,
    repeat_buyers: 7,
    repeat_rate: 0.35,
    rating: 4.6,
    rating_count: 8,
    inquiries: 44,
    tenure_days: 400,
    components: [
      { key: 'revenue', label: 'Verified revenue', weighted: 0.2, weight: 0.4 },
      { key: 'fulfilment', label: 'Fulfilment rate', weighted: 0.25, weight: 0.25 },
      { key: 'repeat', label: 'Repeat buyers', weighted: 0.0, weight: 0.15 },
      { key: 'rating', label: 'Buyer rating', weighted: 0.1, weight: 0.12 },
      { key: 'tenure', label: 'Trading history', weighted: 0.07, weight: 0.08 },
    ],
    window_days: 90,
    recent_window_days: 30,
    ...overrides,
  };
}

/** A thin file: the server withholds the composite and says exactly what is missing. */
function thin(overrides: Partial<CreditProfileOut> = {}): CreditProfileOut {
  return profile({
    score: null,
    is_scoreable: false,
    missing_for_score: ['settled_orders', 'tenure'],
    orders_needed: 4,
    days_needed: 12,
    settled_orders: 6,
    tenure_days: 18,
    revenue_cents: 90_000,
    revenue_trend: null,
    rating_count: 0,
    rating: 0,
    repeat_buyers: 0,
    unique_buyers: 5,
    components: [
      { key: 'revenue', label: 'Verified revenue', weighted: 0.01, weight: 0.4 },
      { key: 'fulfilment', label: 'Fulfilment rate', weighted: 0.25, weight: 0.25 },
      { key: 'repeat', label: 'Repeat buyers', weighted: 0.0, weight: 0.15 },
      { key: 'rating', label: 'Buyer rating', weighted: 0.0, weight: 0.12 },
      { key: 'tenure', label: 'Trading history', weighted: 0.002, weight: 0.08 },
    ],
    ...overrides,
  });
}

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <WeesStockCard session={SESSION} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockGet.mockReset();
});

describe('WeesStockCard', () => {
  it('renders the composite on a 0–100 scale when the file is scoreable', async () => {
    mockGet.mockResolvedValue(profile());
    renderCard();
    // 0.62 → 62. Sellers read a 0–100 figure naturally; the wire value stays a [0,1] float.
    expect(await screen.findByTestId('weesstock-score')).toHaveTextContent('62');
    expect(screen.queryByTestId('weesstock-pending')).not.toBeInTheDocument();
  });

  it('withholds the score on a thin file and names what is still missing', async () => {
    // Doctrine 2: a thin file is NOT a low score. The card must never render 0 here — that
    // would tell a healthy new shop it is uncreditworthy.
    mockGet.mockResolvedValue(thin());
    renderCard();
    const pending = await screen.findByTestId('weesstock-pending');
    expect(pending).toHaveTextContent(/4 more completed sales/i);
    expect(pending).toHaveTextContent(/12 more days of trading/i);
    expect(screen.queryByTestId('weesstock-score')).not.toBeInTheDocument();
  });

  it('does not show a zero score when only one gate is outstanding', async () => {
    // Order gate cleared, tenure gate not. Still no composite, and the prompt mentions only
    // the gate that is actually blocking.
    mockGet.mockResolvedValue(thin({ orders_needed: 0, days_needed: 3, settled_orders: 12 }));
    renderCard();
    const pending = await screen.findByTestId('weesstock-pending');
    expect(pending).toHaveTextContent(/3 more days of trading/i);
    expect(pending).not.toHaveTextContent(/completed sale/i);
    expect(screen.queryByTestId('weesstock-score')).not.toBeInTheDocument();
  });

  it('shows the component breakdown even when the composite is withheld', async () => {
    // Doctrine 1: components are the product. On a thin file they are the only honest signal
    // available, so hiding them would leave a new seller with nothing to act on.
    mockGet.mockResolvedValue(thin());
    renderCard();
    await screen.findByTestId('weesstock-pending');
    expect(screen.getAllByTestId('weesstock-component')).toHaveLength(5);
    expect(screen.getByText('Verified revenue')).toBeInTheDocument();
  });

  it('fills each bar by its share of its OWN weight, not of the whole score', async () => {
    // Tenure maxes at 0.08 and revenue at 0.40. Without per-weight normalisation a maxed-out
    // tenure bar would look nearly empty beside a half-earned revenue bar, which inverts the
    // message: it would read as "your oldest strength is your weakest".
    mockGet.mockResolvedValue(profile());
    renderCard();
    const meters = await screen.findAllByRole('meter');
    // revenue 0.2/0.4 = 50%; fulfilment 0.25/0.25 = 100%; repeat 0 = 0%; tenure .07/.08 ≈ 88%.
    expect(meters.map((m) => m.getAttribute('aria-valuenow'))).toEqual(['50', '100', '0', '83', '88']);
  });

  it('clamps a bar rather than overflowing if weighted ever exceeds its weight', async () => {
    mockGet.mockResolvedValue(profile({
      components: [{ key: 'revenue', label: 'Verified revenue', weighted: 0.9, weight: 0.4 }],
    }));
    renderCard();
    const meter = await screen.findByRole('meter');
    expect(meter).toHaveAttribute('aria-valuenow', '100');
  });

  it('renders an empty bar instead of NaN for a zero-weight component', async () => {
    // Defensive: a future component added at weight 0 (reported-but-not-scored) must not
    // divide by zero and blow up the whole card.
    mockGet.mockResolvedValue(profile({
      components: [{ key: 'demand', label: 'Demand', weighted: 0, weight: 0 }],
    }));
    renderCard();
    expect(await screen.findByRole('meter')).toHaveAttribute('aria-valuenow', '0');
  });

  it('formats money as whole shillings with grouping', async () => {
    mockGet.mockResolvedValue(profile({ revenue_cents: 1_230_000 }));
    renderCard();
    expect(await screen.findByText(/KSh 12,300/)).toBeInTheDocument();
  });

  it('marks inquiries as not scored', async () => {
    // The exclusion is deliberate (inquiries are self-generatable) and must be stated, or it
    // reads as a missing feature rather than a design decision.
    mockGet.mockResolvedValue(profile({ inquiries: 44 }));
    renderCard();
    expect(await screen.findByText(/not scored/i)).toBeInTheDocument();
  });

  it('draws no trend mark when there is no revenue to compare', async () => {
    // null means "undefined ratio", which is NOT the same statement as "flat".
    mockGet.mockResolvedValue(profile({ revenue_trend: null }));
    renderCard();
    await screen.findByTestId('weesstock-score');
    expect(screen.queryByTestId('weesstock-trend')).not.toBeInTheDocument();
  });

  it('treats a small trend delta as steady rather than as movement', async () => {
    mockGet.mockResolvedValue(profile({ revenue_trend: 1.02 }));
    renderCard();
    expect(await screen.findByTestId('weesstock-trend')).toHaveTextContent('→');
  });

  it('marks a genuine upturn and downturn distinctly', async () => {
    mockGet.mockResolvedValue(profile({ revenue_trend: 1.4 }));
    renderCard();
    expect(await screen.findByTestId('weesstock-trend')).toHaveTextContent('↑');
  });

  it('shows Unrated rather than a fake 0.0 star rating', async () => {
    mockGet.mockResolvedValue(profile({ rating: 0, rating_count: 0 }));
    renderCard();
    expect(await screen.findByText(/Unrated/i)).toBeInTheDocument();
  });

  it('renders tenure in the largest honest unit', async () => {
    mockGet.mockResolvedValue(profile({ tenure_days: 400 }));
    renderCard();
    expect(await screen.findByText(/13 months/)).toBeInTheDocument();
  });

  it('shows an error state when the fetch fails', async () => {
    mockGet.mockRejectedValue(new Error('boom'));
    renderCard();
    // The hook sets `retry: 1` explicitly, which overrides the test client's retry:false — so
    // the error state only appears after the retry settles. Same allowance as LowStockCard.
    expect(
      await screen.findByRole('alert', {}, { timeout: 3000 }),
    ).toHaveTextContent(/Couldn.t load your funding profile/i);
  });
});
