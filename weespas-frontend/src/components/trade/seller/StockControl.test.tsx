import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../../api/commerce')>('../../../api/commerce');
  return { ...actual, adjustStock: vi.fn() };
});

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ toast }) }));

import { adjustStock } from '../../../api/commerce';
import StockControl from './StockControl';
import type { CommerceSession, ListingOut } from '../../../api/commerce';

const mockAdjust = vi.mocked(adjustStock);
const SESSION: CommerceSession = { token: 'ctok', commerce_url: 'http://c' };

function listingOut(qty: number): ListingOut {
  return {
    id: 'l1', shop_id: 's1', seller_id: 'sel1', property_uuid: null, title: 'X', description: null,
    price_cents: 100, currency: 'KES', media_urls: [], intent_weight: 1, is_active: true,
    stock_qty: qty, low_stock_threshold: 0, pricing_mode: 'fixed', is_short_video: false, post_kind: 'product',
    is_out_of_stock: qty <= 0, is_low_stock: false, promo_mode: null, promo_started_at: null,
    promo_expires_at: null, is_promoted: false,
    flash_price_cents: null, flash_started_at: null, flash_expires_at: null, flash_reference_cents: null, is_flash_active: false,
    created_at: '2026-06-29T00:00:00Z',
  };
}

function renderControl(stockQty = 5) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <StockControl session={SESSION} listingId="l1" stockQty={stockQty} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockAdjust.mockResolvedValue(listingOut(4));
});

describe('StockControl — sends exactly one StockAdjust shape per action', () => {
  it('the +1 button sends {delta: 1} only', async () => {
    renderControl(5);
    fireEvent.click(screen.getByTestId('stock-plus'));
    await waitFor(() => expect(mockAdjust).toHaveBeenCalledTimes(1));
    expect(mockAdjust.mock.calls[0][2]).toEqual({ delta: 1 });
  });

  it('the −1 button sends {delta: -1} only', async () => {
    renderControl(5);
    fireEvent.click(screen.getByTestId('stock-minus'));
    await waitFor(() => expect(mockAdjust).toHaveBeenCalledTimes(1));
    expect(mockAdjust.mock.calls[0][2]).toEqual({ delta: -1 });
  });

  it('Set sends {stock_qty: n} only (absolute), never a delta', async () => {
    renderControl(5);
    fireEvent.change(screen.getByLabelText('Set absolute stock'), { target: { value: '12' } });
    fireEvent.click(screen.getByTestId('stock-set'));
    await waitFor(() => expect(mockAdjust).toHaveBeenCalledTimes(1));
    expect(mockAdjust.mock.calls[0][2]).toEqual({ stock_qty: 12 });
  });

  it('disables −1 at zero stock (cannot go negative)', () => {
    renderControl(0);
    expect(screen.getByTestId('stock-minus')).toBeDisabled();
    expect(screen.getByTestId('stock-plus')).not.toBeDisabled();
  });

  it('rejects a non-numeric absolute value without calling the API', async () => {
    renderControl(5);
    fireEvent.change(screen.getByLabelText('Set absolute stock'), { target: { value: 'abc' } });
    fireEvent.click(screen.getByTestId('stock-set'));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(mockAdjust).not.toHaveBeenCalled();
  });
});
