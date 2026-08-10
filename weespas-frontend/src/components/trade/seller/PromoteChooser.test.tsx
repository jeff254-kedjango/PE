import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../../api/commerce')>('../../../api/commerce');
  return { ...actual, promoteListing: vi.fn(), clearPromotion: vi.fn() };
});

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ toast }) }));

import { promoteListing, clearPromotion } from '../../../api/commerce';
import PromoteChooser from './PromoteChooser';
import type { CommerceSession, ListingOut } from '../../../api/commerce';

const mockPromote = vi.mocked(promoteListing);
const mockClear = vi.mocked(clearPromotion);
const SESSION: CommerceSession = { token: 'ctok', commerce_url: 'http://c' };

function listing(overrides: Partial<ListingOut> = {}): ListingOut {
  return {
    id: 'l1', shop_id: 's1', seller_id: 'sel1', property_uuid: null, title: 'Sukuma', description: null,
    price_cents: 100, currency: 'KES', media_urls: [], intent_weight: 1, is_active: true,
    stock_qty: 5, low_stock_threshold: 0, pricing_mode: 'fixed', is_short_video: false, post_kind: 'product',
    is_out_of_stock: false, is_low_stock: false, promo_mode: null, promo_started_at: null,
    promo_expires_at: null, is_promoted: false,
    flash_price_cents: null, flash_started_at: null, flash_expires_at: null, flash_reference_cents: null, is_flash_active: false,
    created_at: '2026-06-29T00:00:00Z', ...overrides,
  };
}

function renderChooser(li = listing()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <PromoteChooser session={SESSION} listing={li} onClose={() => {}} />
    </QueryClientProvider>,
  );
}

const submit = () => fireEvent.submit(document.getElementById('promote-form') as HTMLFormElement);

beforeEach(() => {
  vi.clearAllMocks();
  mockPromote.mockResolvedValue(listing({ is_promoted: true, promo_mode: 'story' }));
});

describe('PromoteChooser', () => {
  it('sends the selected mode + duration to promoteListing', async () => {
    renderChooser();
    fireEvent.click(screen.getByTestId('promo-mode-story'));
    fireEvent.change(screen.getByTestId('promo-duration'), { target: { value: '3600' } });
    submit();
    await waitFor(() => expect(mockPromote).toHaveBeenCalledTimes(1));
    expect(mockPromote.mock.calls[0][1]).toBe('l1');
    expect(mockPromote.mock.calls[0][2]).toEqual({ mode: 'story', duration_seconds: 3600 });
  });

  it('defaults the mode to evergreen and a sane duration', async () => {
    renderChooser();
    submit();
    await waitFor(() => expect(mockPromote).toHaveBeenCalledTimes(1));
    expect(mockPromote.mock.calls[0][2]).toEqual({ mode: 'evergreen', duration_seconds: 86400 });
  });

  it('offers Clear promotion only when the listing is already promoted', () => {
    const { rerender } = (() => {
      const qc = new QueryClient();
      return render(
        <QueryClientProvider client={qc}>
          <PromoteChooser session={SESSION} listing={listing()} onClose={() => {}} />
        </QueryClientProvider>,
      );
    })();
    expect(screen.queryByRole('button', { name: 'Clear promotion' })).toBeNull();
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <PromoteChooser session={SESSION} listing={listing({ is_promoted: true, promo_mode: 'evergreen' })} onClose={() => {}} />
      </QueryClientProvider>,
    );
    expect(screen.getByRole('button', { name: 'Clear promotion' })).toBeInTheDocument();
  });

  it('Clear promotion calls clearPromotion', async () => {
    mockClear.mockResolvedValue(listing({ is_promoted: false }));
    renderChooser(listing({ is_promoted: true, promo_mode: 'evergreen', promo_expires_at: '2026-06-30T00:00:00Z' }));
    fireEvent.click(screen.getByRole('button', { name: 'Clear promotion' }));
    await waitFor(() => expect(mockClear).toHaveBeenCalledTimes(1));
    expect(mockClear.mock.calls[0][1]).toBe('l1');
  });
});
