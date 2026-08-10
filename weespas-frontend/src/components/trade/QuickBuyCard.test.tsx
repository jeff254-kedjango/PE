import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../api/commerce')>('../../api/commerce');
  return { ...actual, openOrder: vi.fn() };
});

import { openOrder, type CommerceSession, type QuickBuyItem, type OrderOut } from '../../api/commerce';
import QuickBuyCard from './QuickBuyCard';

const mockOpenOrder = vi.mocked(openOrder);
const SESSION: CommerceSession = { token: 'tok', commerce_url: 'http://c' };

function item(over: Partial<QuickBuyItem> = {}): QuickBuyItem {
  return {
    id: 'l1', shop_id: 'sh1', seller_id: 'sel1', shop_name: 'Mama Mboga', shop_category: 'general',
    title: 'Fresh Sukuma', price_cents: 5000, currency: 'KES',
    thumbnail_url: '/uploads/trade/images/p.webp', distance_m: 800, pricing_mode: 'fixed',
    bucket: 'near',
    ...over,
  };
}

const order: OrderOut = {
  id: 'o1', listing_id: 'l1', status: 'PRICE_LOCKED', pricing_mode: 'fixed',
  reference_price_cents: 5000, locked_price_cents: 5000, current_offer_cents: null,
  created_at: '2026-07-01T00:00:00Z',
};

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); });

describe('QuickBuyCard', () => {
  it('renders title, price and a cart button', () => {
    render(<QuickBuyCard item={item()} session={SESSION} onSelectSeller={vi.fn()} />);
    expect(screen.getByText('Fresh Sukuma')).toBeInTheDocument();
    expect(screen.getByText(/KES/)).toBeInTheDocument();
    expect(screen.getByTestId('quick-buy-cart')).toBeInTheDocument();
  });

  it('FIXED price → cart button opens an order (buy now) and shows Placed', async () => {
    mockOpenOrder.mockResolvedValue(order);
    render(<QuickBuyCard item={item({ pricing_mode: 'fixed' })} session={SESSION} onSelectSeller={vi.fn()} />);
    fireEvent.click(screen.getByTestId('quick-buy-cart'));
    await waitFor(() => expect(mockOpenOrder).toHaveBeenCalledTimes(1));
    // Called with (session, listingId, idempotencyKey) — a non-empty key on the money path.
    const [, listingId, idemKey] = mockOpenOrder.mock.calls[0];
    expect(listingId).toBe('l1');
    expect(typeof idemKey).toBe('string');
    expect(idemKey.length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.getByText('✓ Placed')).toBeInTheDocument());
  });

  it('BARGAIN → cart button does NOT open an order; it opens the storefront to negotiate', () => {
    const onSelect = vi.fn();
    render(<QuickBuyCard item={item({ pricing_mode: 'bargain' })} session={SESSION} onSelectSeller={onSelect} />);
    fireEvent.click(screen.getByTestId('quick-buy-cart'));
    expect(mockOpenOrder).not.toHaveBeenCalled();
    expect(onSelect).toHaveBeenCalledWith('sel1');
  });

  it('tapping the card body opens the seller storefront', () => {
    const onSelect = vi.fn();
    render(<QuickBuyCard item={item()} session={SESSION} onSelectSeller={onSelect} />);
    fireEvent.click(screen.getByText('Fresh Sukuma'));
    expect(onSelect).toHaveBeenCalledWith('sel1');
  });

  it('shows an initials fallback when there is no thumbnail', () => {
    render(<QuickBuyCard item={item({ thumbnail_url: null })} session={SESSION} onSelectSeller={vi.fn()} />);
    expect(screen.getByText('F')).toBeInTheDocument(); // "Fresh Sukuma" → F
  });

  it('surfaces an error state when the order fails, then allows retry', async () => {
    mockOpenOrder.mockRejectedValueOnce(new Error('boom'));
    render(<QuickBuyCard item={item()} session={SESSION} onSelectSeller={vi.fn()} />);
    fireEvent.click(screen.getByTestId('quick-buy-cart'));
    await waitFor(() => expect(screen.getByText('Try again')).toBeInTheDocument());
  });
});
