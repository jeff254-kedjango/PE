import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../api/commerce')>('../../api/commerce');
  return { ...actual, openOrder: vi.fn() };
});

import { openOrder, type CommerceSession, type FlashSaleItem, type OrderOut } from '../../api/commerce';
import FlashSaleCard from './FlashSaleCard';

const mockOpenOrder = vi.mocked(openOrder);
const SESSION: CommerceSession = { token: 'tok', commerce_url: 'http://c' };

function item(over: Partial<FlashSaleItem> = {}): FlashSaleItem {
  return {
    id: 'l1', shop_id: 'sh1', seller_id: 'sel1', shop_name: 'Sneaker Hub', shop_category: 'shoes',
    title: 'Air Jordan', flash_price_cents: 10000, reference_cents: 100000, discount_percent: 90,
    currency: 'KES', thumbnail_url: '/uploads/trade/images/p.webp', expires_at: '2026-07-01T01:00:00Z',
    distance_m: 800, pricing_mode: 'fixed',
    ...over,
  };
}

const order: OrderOut = {
  id: 'o1', listing_id: 'l1', status: 'PRICE_LOCKED', pricing_mode: 'fixed',
  reference_price_cents: 100000, locked_price_cents: 10000, current_offer_cents: null,
  created_at: '2026-07-01T00:00:00Z',
};

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); });

describe('FlashSaleCard', () => {
  it('renders title, flash price, the struck reference and the discount badge', () => {
    render(<FlashSaleCard item={item()} session={SESSION} onSelectSeller={vi.fn()} />);
    expect(screen.getByText('Air Jordan')).toBeInTheDocument();
    // formatPrice abbreviates: flash 10000c → "KES 100"; reference 100000c → "KES 1K" (struck).
    expect(screen.getByText('KES 100')).toBeInTheDocument();
    expect(screen.getByText('KES 1K')).toBeInTheDocument();
    expect(screen.getByText(/90%/)).toBeInTheDocument();
  });

  it('one tap opens an order (buy now) with a non-empty idempotency key and shows Placed', async () => {
    mockOpenOrder.mockResolvedValue(order);
    render(<FlashSaleCard item={item()} session={SESSION} onSelectSeller={vi.fn()} />);
    fireEvent.click(screen.getByTestId('flash-sale-buy'));
    await waitFor(() => expect(mockOpenOrder).toHaveBeenCalledTimes(1));
    const [, listingId, idemKey] = mockOpenOrder.mock.calls[0];
    expect(listingId).toBe('l1');
    expect(typeof idemKey).toBe('string');
    expect(idemKey.length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.getByText('✓ Placed')).toBeInTheDocument());
  });

  it('tapping the card body opens the seller storefront', () => {
    const onSelect = vi.fn();
    render(<FlashSaleCard item={item()} session={SESSION} onSelectSeller={onSelect} />);
    fireEvent.click(screen.getByText('Air Jordan'));
    expect(onSelect).toHaveBeenCalledWith('sel1');
  });

  it('hides the strikethrough and badge when the reference is not above the flash price', () => {
    const { container } = render(
      <FlashSaleCard item={item({ reference_cents: 10000, discount_percent: 0 })} session={SESSION} onSelectSeller={vi.fn()} />,
    );
    // reference == flash ⇒ no struck reference, and a 0% discount ⇒ no badge.
    expect(container.querySelector('.flash-sale-card__ref')).toBeNull();
    expect(container.querySelector('.flash-sale-card__badge')).toBeNull();
  });

  it('shows an initials fallback when there is no thumbnail', () => {
    render(<FlashSaleCard item={item({ thumbnail_url: null })} session={SESSION} onSelectSeller={vi.fn()} />);
    expect(screen.getByText('A')).toBeInTheDocument(); // "Air Jordan" → A
  });

  it('surfaces an error state when the order fails, then allows retry', async () => {
    mockOpenOrder.mockRejectedValueOnce(new Error('boom'));
    render(<FlashSaleCard item={item()} session={SESSION} onSelectSeller={vi.fn()} />);
    fireEvent.click(screen.getByTestId('flash-sale-buy'));
    await waitFor(() => expect(screen.getByText('Try again')).toBeInTheDocument());
  });
});
