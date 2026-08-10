import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../../api/commerce')>('../../../api/commerce');
  return { ...actual, launchFlashSale: vi.fn(), clearFlashSale: vi.fn() };
});

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ toast }) }));

import { launchFlashSale, clearFlashSale } from '../../../api/commerce';
import FlashSaleChooser from './FlashSaleChooser';
import type { CommerceSession, ListingOut } from '../../../api/commerce';

const mockLaunch = vi.mocked(launchFlashSale);
const mockClear = vi.mocked(clearFlashSale);
const SESSION: CommerceSession = { token: 'ctok', commerce_url: 'http://c' };

function listing(overrides: Partial<ListingOut> = {}): ListingOut {
  return {
    id: 'l1', shop_id: 's1', seller_id: 'sel1', property_uuid: null, title: 'Air Jordan', description: null,
    price_cents: 100000, currency: 'KES', media_urls: [], intent_weight: 1, is_active: true,
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
      <FlashSaleChooser session={SESSION} listing={li} onClose={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('FlashSaleChooser', () => {
  it('offers only durations within the 1-hour cap', () => {
    renderChooser();
    const select = screen.getByTestId('flash-duration') as HTMLSelectElement;
    const values = Array.from(select.options).map((o) => Number(o.value));
    expect(values.every((v) => v > 0 && v <= 3600)).toBe(true);
    expect(values).toContain(3600); // the 1-hour max preset
  });

  it('converts the major-unit price to cents and launches', async () => {
    mockLaunch.mockResolvedValue(listing({ is_flash_active: true, flash_price_cents: 1000 }));
    renderChooser();
    fireEvent.change(screen.getByTestId('flash-price'), { target: { value: '10' } });
    fireEvent.change(screen.getByTestId('flash-duration'), { target: { value: '1800' } });
    fireEvent.click(screen.getByTestId('flash-submit'));
    await waitFor(() => expect(mockLaunch).toHaveBeenCalledTimes(1));
    const [, listingId, body] = mockLaunch.mock.calls[0];
    expect(listingId).toBe('l1');
    expect(body).toEqual({ flash_price_cents: 1000, duration_seconds: 1800 }); // KES 10 → 1000 cents
  });

  it('rejects a non-positive price locally (no request) and toasts', () => {
    renderChooser();
    fireEvent.change(screen.getByTestId('flash-price'), { target: { value: '0' } });
    fireEvent.click(screen.getByTestId('flash-submit'));
    expect(mockLaunch).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalled();
  });

  it('shows Clear only when a flash sale is already active, and clears it', async () => {
    mockClear.mockResolvedValue(listing({ is_flash_active: false }));
    renderChooser(listing({ is_flash_active: true, flash_expires_at: '2026-07-01T01:00:00Z' }));
    const clearBtn = screen.getByTestId('flash-clear');
    expect(clearBtn).toBeInTheDocument();
    fireEvent.click(clearBtn);
    await waitFor(() => expect(mockClear).toHaveBeenCalledTimes(1));
  });

  it('does not show Clear when there is no active flash sale', () => {
    renderChooser();
    expect(screen.queryByTestId('flash-clear')).not.toBeInTheDocument();
  });
});
