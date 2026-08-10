import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

// Mock the data hook so we control the slate deterministically (no network / react-query).
vi.mock('../../hooks/useFlashSales', () => ({ useFlashSales: vi.fn() }));
// Mock openOrder so a buy-now in a card here is inert.
vi.mock('../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../api/commerce')>('../../api/commerce');
  return { ...actual, openOrder: vi.fn() };
});

import { useFlashSales } from '../../hooks/useFlashSales';
import type { CommerceSession, FlashSaleItem, FlashSalesResponse } from '../../api/commerce';
import FlashSales from './FlashSales';

const mockUseFlashSales = vi.mocked(useFlashSales);
const SESSION: CommerceSession = { token: 'tok', commerce_url: 'http://c' };

function mkItems(n: number): FlashSaleItem[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `l${i}`, shop_id: 'sh1', seller_id: `sel${i}`, shop_name: 'Shop', shop_category: 'shoes',
    title: `Item ${i}`, flash_price_cents: 1000 + i, reference_cents: 10000, discount_percent: 90,
    currency: 'KES', thumbnail_url: '/uploads/trade/images/p.webp',
    expires_at: '2026-07-01T01:00:00Z', distance_m: 500 + i, pricing_mode: 'fixed',
  }));
}

function setSlate(items: FlashSaleItem[], over: Partial<FlashSalesResponse> = {}) {
  mockUseFlashSales.mockReturnValue({
    data: { items, page_size: 6, ...over },
    isLoading: false,
    isError: false,
  });
}

function renderFS(onSelect = vi.fn()) {
  return {
    onSelect,
    ...render(<FlashSales session={SESSION} lat={-1.29} lng={36.82} onSelectSeller={onSelect} />),
  };
}

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); });

describe('FlashSales', () => {
  it('renders the section header "Flash Sales" and the "expires in less than an hour" subtitle', () => {
    setSlate(mkItems(6));
    renderFS();
    expect(screen.getByRole('heading', { name: /Flash Sales/ })).toBeInTheDocument();
    expect(screen.getByText('expires in less than an hour')).toBeInTheDocument();
  });

  it('renders one page (page_size = 6, a 3×2 grid) of cards at a time', () => {
    setSlate(mkItems(10)); // 2 pages of 6
    renderFS();
    const grid = screen.getByTestId('flash-sales-grid');
    expect(within(grid).getAllByTestId('flash-sale-buy')).toHaveLength(6);
  });

  // Chunk 1 (permanent columns): the section STAYS MOUNTED with a placeholder when there are no
  // flash sales, so the right column keeps its width on load. The urgency subtitle is REPLACED
  // with "None right now" — the "expires in less than an hour" claim is only honest when items
  // are on display.
  it('stays mounted with a placeholder when there are no flash sales', () => {
    setSlate([]);
    renderFS();
    // Header remains so the column has a stable anchor.
    expect(screen.getByRole('heading', { name: /Flash Sales/ })).toBeInTheDocument();
    // Honest empty copy + no grid.
    expect(screen.getByTestId('flash-sales-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('flash-sales-grid')).toBeNull();
    // Urgency claim is suppressed in the empty state.
    expect(screen.queryByText('expires in less than an hour')).toBeNull();
    expect(screen.getByText('None right now')).toBeInTheDocument();
  });

  it('prev is disabled on the first page; next advances the page', () => {
    setSlate(mkItems(10));
    renderFS();
    const prev = screen.getByRole('button', { name: 'Previous Flash Sales' });
    const next = screen.getByRole('button', { name: 'Next Flash Sales' });
    expect(prev).toBeDisabled();
    expect(next).toBeEnabled();
    expect(screen.getByText('1 / 2')).toBeInTheDocument();

    fireEvent.click(next);
    expect(screen.getByText('2 / 2')).toBeInTheDocument();
    // On the last page the grid shows the remaining 4 cards and next is disabled.
    expect(within(screen.getByTestId('flash-sales-grid')).getAllByTestId('flash-sale-buy')).toHaveLength(4);
    expect(next).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Previous Flash Sales' })).toBeEnabled();
  });

  it('no nav is shown when a single page fits everything', () => {
    setSlate(mkItems(6));
    renderFS();
    expect(screen.queryByRole('button', { name: 'Next Flash Sales' })).not.toBeInTheDocument();
  });
});
