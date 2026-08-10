import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../../api/commerce')>('../../../api/commerce');
  return { ...actual, getBoostTiers: vi.fn(), getBoostAllowances: vi.fn(), createBoost: vi.fn(), revokeBoost: vi.fn() };
});

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ toast }) }));

import { getBoostTiers, getBoostAllowances, createBoost } from '../../../api/commerce';
import BoostChooser from './BoostChooser';
import type {
  CommerceSession, ListingOut, BoostAllowancesOut, BoostGrantOut, BoostTiersOut,
} from '../../../api/commerce';

const mockTiers = vi.mocked(getBoostTiers);
const mockAllowances = vi.mocked(getBoostAllowances);
const mockCreate = vi.mocked(createBoost);
const SESSION: CommerceSession = { token: 'ctok', commerce_url: 'http://c' };

// Server-authoritative catalogue (order = narrow → wide, exactly as the backend returns it). The
// chooser must render reach km / order from THIS — not a hard-coded FE constant.
function tiers(): BoostTiersOut {
  return {
    tiers: [
      { tier: 'mtaa', scope_kind: 'radius', radius_m: 10000, daily_free_cap: 10, duration_default_seconds: 86400, price_kes: 0 },
      { tier: 'hustle', scope_kind: 'radius', radius_m: 50000, daily_free_cap: 8, duration_default_seconds: 86400, price_kes: 0 },
      { tier: 'sovereign', scope_kind: 'nation', radius_m: null, daily_free_cap: 3, duration_default_seconds: 86400, price_kes: 0 },
    ],
  };
}

function listing(): ListingOut {
  return {
    id: 'l1', shop_id: 's1', seller_id: 'sel1', property_uuid: null, title: 'Sukuma', description: null,
    price_cents: 100, currency: 'KES', media_urls: [], intent_weight: 1, is_active: true,
    stock_qty: 5, low_stock_threshold: 0, pricing_mode: 'fixed', is_short_video: false, post_kind: 'product',
    is_out_of_stock: false, is_low_stock: false, promo_mode: null, promo_started_at: null,
    promo_expires_at: null, is_promoted: false,
    flash_price_cents: null, flash_started_at: null, flash_expires_at: null, flash_reference_cents: null, is_flash_active: false,
    created_at: '2026-06-29T00:00:00Z',
  };
}

function allowances(overrides: Partial<Record<'mtaa' | 'hustle' | 'sovereign', number>> = {}): BoostAllowancesOut {
  const rem = { mtaa: 10, hustle: 8, sovereign: 3, ...overrides };
  return {
    business_date: '2026-06-29',
    tiers: [
      { tier: 'mtaa', daily_cap: 10, remaining: rem.mtaa },
      { tier: 'hustle', daily_cap: 8, remaining: rem.hustle },
      { tier: 'sovereign', daily_cap: 3, remaining: rem.sovereign },
    ],
  };
}

function grant(tier: 'mtaa' | 'hustle' | 'sovereign'): BoostGrantOut {
  return {
    id: 'g1', seller_id: 'sel1', target_type: 'listing', target_id: 'l1', tier,
    scope_kind: tier === 'sovereign' ? 'nation' : 'radius', radius_m: tier === 'sovereign' ? null : 10000,
    started_at: '2026-06-29T00:00:00Z', expires_at: '2026-06-30T00:00:00Z',
    business_date: '2026-06-29', source: 'free',
  };
}

function renderChooser() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <BoostChooser session={SESSION} listing={listing()} onClose={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockTiers.mockResolvedValue(tiers());
  mockAllowances.mockResolvedValue(allowances());
});

describe('BoostChooser', () => {
  it('shows each tier with its live remaining/cap', async () => {
    renderChooser();
    await waitFor(() => expect(screen.getByTestId('boost-remaining-mtaa')).toBeInTheDocument());
    expect(screen.getByTestId('boost-remaining-mtaa').textContent).toContain('10 of 10');
    expect(screen.getByTestId('boost-remaining-sovereign').textContent).toContain('3 of 3');
  });

  it('renders reach km from the SERVER catalogue, not a hard-coded FE constant', async () => {
    // The backend is authoritative: if it says hustle is 42 km, the chooser shows 42 km. This is the
    // anti-drift guarantee — a hard-coded "50 km" would fail this test.
    mockTiers.mockResolvedValue({
      tiers: [
        { tier: 'mtaa', scope_kind: 'radius', radius_m: 7000, daily_free_cap: 10, duration_default_seconds: 86400, price_kes: 0 },
        { tier: 'hustle', scope_kind: 'radius', radius_m: 42000, daily_free_cap: 8, duration_default_seconds: 86400, price_kes: 0 },
        { tier: 'sovereign', scope_kind: 'nation', radius_m: null, daily_free_cap: 3, duration_default_seconds: 86400, price_kes: 0 },
      ],
    });
    renderChooser();
    await waitFor(() => expect(screen.getByTestId('boost-tier-hustle')).toBeInTheDocument());
    expect(screen.getByText(/7 km/)).toBeInTheDocument();
    expect(screen.getByText(/42 km/)).toBeInTheDocument();
    expect(screen.getByText(/Nationwide/)).toBeInTheDocument();
  });

  it('shows a nominal price only when the server sets one (0 ⇒ hidden)', async () => {
    mockTiers.mockResolvedValue({
      tiers: [
        { tier: 'mtaa', scope_kind: 'radius', radius_m: 10000, daily_free_cap: 10, duration_default_seconds: 86400, price_kes: 0 },
        { tier: 'hustle', scope_kind: 'radius', radius_m: 50000, daily_free_cap: 8, duration_default_seconds: 86400, price_kes: 150 },
        { tier: 'sovereign', scope_kind: 'nation', radius_m: null, daily_free_cap: 3, duration_default_seconds: 86400, price_kes: 0 },
      ],
    });
    renderChooser();
    await waitFor(() => expect(screen.getByTestId('boost-tier-hustle')).toBeInTheDocument());
    expect(screen.getByTestId('boost-price-hustle').textContent).toContain('KES 150');
    expect(screen.queryByTestId('boost-price-mtaa')).not.toBeInTheDocument();
  });

  it('disables a tier whose remaining is 0 (cannot click into a 429)', async () => {
    mockAllowances.mockResolvedValue(allowances({ sovereign: 0 }));
    renderChooser();
    // Wait for allowances to LOAD (quota text present) — before that, every tier is disabled.
    await waitFor(() => expect(screen.getByTestId('boost-remaining-mtaa')).toBeInTheDocument());
    expect(screen.getByTestId('boost-tier-sovereign')).toBeDisabled();
    expect(screen.getByTestId('boost-tier-mtaa')).not.toBeDisabled();
  });

  it('boosting calls createBoost with the right tier and refetches allowances on success', async () => {
    mockCreate.mockResolvedValue(grant('mtaa'));
    renderChooser();
    await waitFor(() => expect(screen.getByTestId('boost-remaining-mtaa')).toBeInTheDocument());
    // Second allowances fetch (the post-success refetch) returns a decremented count.
    mockAllowances.mockResolvedValue(allowances({ mtaa: 9 }));
    fireEvent.click(screen.getByTestId('boost-tier-mtaa'));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate.mock.calls[0][1]).toEqual({ target_type: 'listing', target_id: 'l1', tier: 'mtaa' });
    // Allowance query invalidated → refetched → count visibly decrements.
    await waitFor(() => expect(screen.getByTestId('boost-remaining-mtaa').textContent).toContain('9 of 10'));
    // The just-opened grant can be stopped.
    expect(screen.getByTestId('boost-stop')).toBeInTheDocument();
  });
});
