import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

// Mock the data hook so we control the slate deterministically (no network / react-query).
vi.mock('../../hooks/useQuickBuys', () => ({ useQuickBuys: vi.fn() }));
// Mock openOrder so a buy-now in a card here is inert.
vi.mock('../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../api/commerce')>('../../api/commerce');
  return { ...actual, openOrder: vi.fn() };
});

import { useQuickBuys } from '../../hooks/useQuickBuys';
import type { CommerceSession, QuickBuyItem, QuickBuysResponse } from '../../api/commerce';
import QuickBuys from './QuickBuys';

const mockUseQuickBuys = vi.mocked(useQuickBuys);
const SESSION: CommerceSession = { token: 'tok', commerce_url: 'http://c' };

function mkItems(n: number): QuickBuyItem[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `l${i}`, shop_id: 'sh1', seller_id: `sel${i}`, shop_name: 'Shop', shop_category: 'general',
    title: `Item ${i}`, price_cents: 1000 + i, currency: 'KES',
    thumbnail_url: '/uploads/trade/images/p.webp', distance_m: 500 + i, pricing_mode: 'fixed',
    bucket: i < 4 ? 'near' : 'interest',
  }));
}

function setSlate(items: QuickBuyItem[], over: Partial<QuickBuysResponse> = {}) {
  mockUseQuickBuys.mockReturnValue({
    data: { items, near_radius_m: 5000, page_size: 9, ...over },
    isLoading: false,
    isError: false,
  });
}

function renderQB(onSelect = vi.fn()) {
  return {
    onSelect,
    ...render(<QuickBuys session={SESSION} lat={-1.29} lng={36.82} onSelectSeller={onSelect} />),
  };
}

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); });

describe('QuickBuys', () => {
  it('renders the section header "Quick Buys" and a filter button', () => {
    setSlate(mkItems(9));
    renderQB();
    expect(screen.getByRole('heading', { name: 'Quick Buys' })).toBeInTheDocument();
    expect(screen.getByTestId('quick-buys-filter-open')).toBeInTheDocument();
  });

  it('renders one page (page_size) of cards at a time', () => {
    setSlate(mkItems(15)); // 2 pages of 9
    renderQB();
    const grid = screen.getByTestId('quick-buys-grid');
    // First page shows exactly 9 cards.
    expect(within(grid).getAllByTestId('quick-buy-cart')).toHaveLength(9);
  });

  // Chunk 1 (permanent columns): the section STAYS MOUNTED with an honest empty placeholder when
  // the server confirms zero items and no filter is narrowing. The right column then keeps its
  // width on load instead of collapsing.
  it('stays mounted with a placeholder when there are no items and no active filter', () => {
    setSlate([]);
    renderQB();
    // Header still shows so the column has a stable anchor.
    expect(screen.getByRole('heading', { name: /Quick Buys/i })).toBeInTheDocument();
    // Filter button stays visible — filters are server-side and can genuinely surface items.
    expect(screen.getByTestId('quick-buys-filter-open')).toBeInTheDocument();
    // Honest empty copy is rendered; no real grid.
    expect(screen.getByTestId('quick-buys-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('quick-buys-grid')).toBeNull();
  });

  it('prev is disabled on the first page; next advances the page', () => {
    setSlate(mkItems(15));
    renderQB();
    const prev = screen.getByRole('button', { name: 'Previous Quick Buys' });
    const next = screen.getByRole('button', { name: 'Next Quick Buys' });
    expect(prev).toBeDisabled();
    expect(next).toBeEnabled();
    expect(screen.getByText('1 / 2')).toBeInTheDocument();

    fireEvent.click(next);
    expect(screen.getByText('2 / 2')).toBeInTheDocument();
    // On the last page the grid shows the remaining 6 cards and next is disabled.
    expect(within(screen.getByTestId('quick-buys-grid')).getAllByTestId('quick-buy-cart')).toHaveLength(6);
    expect(next).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Previous Quick Buys' })).toBeEnabled();
  });

  it('no nav is shown when a single page fits everything', () => {
    setSlate(mkItems(6));
    renderQB();
    expect(screen.queryByRole('button', { name: 'Next Quick Buys' })).not.toBeInTheDocument();
  });

  it('the filter button opens the filter modal', () => {
    setSlate(mkItems(9));
    renderQB();
    expect(screen.queryByTestId('quick-buys-filter-modal')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('quick-buys-filter-open'));
    expect(screen.getByTestId('quick-buys-filter-modal')).toBeInTheDocument();
  });

  it('applying a filter in the modal closes it (and drives a refetch via the hook key)', () => {
    setSlate(mkItems(9));
    renderQB();
    fireEvent.click(screen.getByTestId('quick-buys-filter-open'));
    const modal = screen.getByTestId('quick-buys-filter-modal');
    // Pick a category chip + Apply.
    fireEvent.click(within(modal).getByRole('button', { name: 'Butchery' }));
    fireEvent.click(within(modal).getByRole('button', { name: /Apply/ }));
    expect(screen.queryByTestId('quick-buys-filter-modal')).not.toBeInTheDocument();
    // The hook was called again with the categories filter (last call reflects the applied state).
    const calls = mockUseQuickBuys.mock.calls;
    const lastArgs = calls[calls.length - 1][0];
    expect(lastArgs.filters?.categories).toContain('butchery');
  });

  it('shows an empty-state (not null) once a filter is active but yields no items', () => {
    // The slate is empty throughout. With no filter the section shows the AUTHORITATIVE-EMPTY
    // placeholder (see the "stays mounted with a placeholder" test above); after applying a
    // category filter, anyFilterActive flips true, so this branch renders the "no matches — try
    // widening" hint instead. Both branches keep the section mounted.
    setSlate([]);
    // Render with the header visible by seeding one item, then filter to an empty result.
    setSlate(mkItems(1));
    renderQB();
    fireEvent.click(screen.getByTestId('quick-buys-filter-open'));
    const modal = screen.getByTestId('quick-buys-filter-modal');
    fireEvent.click(within(modal).getByRole('button', { name: 'Butchery' }));
    // Flip the hook to an empty result for the applied-filter render.
    setSlate([]);
    fireEvent.click(within(modal).getByRole('button', { name: /Apply/ }));
    expect(screen.getByText(/No matches/i)).toBeInTheDocument();
  });

  // #4b — the in-popover search filters the ALREADY-FETCHED slate by title, client-side, with NO
  // refetch (the hook is not re-invoked with a new key) and no full-page overlay.
  it('search narrows the grid by title client-side without refetching', () => {
    // Two distinct titles so a query can select one. mkItems gives "Item 0".."Item N".
    setSlate(mkItems(9));
    renderQB();
    const callsBefore = mockUseQuickBuys.mock.calls.length;

    fireEvent.click(screen.getByTestId('quick-buys-filter-open'));
    const search = screen.getByTestId('quick-buys-search');
    // "Item 3" matches exactly one card.
    fireEvent.change(search, { target: { value: 'Item 3' } });

    const grid = screen.getByTestId('quick-buys-grid');
    expect(within(grid).getAllByTestId('quick-buy-cart')).toHaveLength(1);
    // The hook was NOT called with any new filter key — search is purely client-side. (React may
    // re-render, but the LAST hook args must still carry the same empty filters, never a search.)
    const lastArgs = mockUseQuickBuys.mock.calls[mockUseQuickBuys.mock.calls.length - 1][0];
    expect(lastArgs.filters).toEqual({});
    expect(mockUseQuickBuys.mock.calls.length).toBeGreaterThanOrEqual(callsBefore);
  });

  it('a search yielding nothing keeps the section mounted with a no-matches hint', () => {
    setSlate(mkItems(5));
    renderQB();
    fireEvent.click(screen.getByTestId('quick-buys-filter-open'));
    fireEvent.change(screen.getByTestId('quick-buys-search'), { target: { value: 'zzz-no-such-title' } });
    expect(screen.getByText(/No matches/i)).toBeInTheDocument();
    // Clearing the search restores the full slate.
    fireEvent.change(screen.getByTestId('quick-buys-search'), { target: { value: '' } });
    expect(within(screen.getByTestId('quick-buys-grid')).getAllByTestId('quick-buy-cart')).toHaveLength(5);
  });

  it('the filter popover is localized (no full-page overlay element)', () => {
    setSlate(mkItems(9));
    renderQB();
    fireEvent.click(screen.getByTestId('quick-buys-filter-open'));
    const popover = screen.getByTestId('quick-buys-filter-modal');
    // It must be a non-modal dialog (aria-modal="false") — it doesn't trap the whole page.
    expect(popover).toHaveAttribute('aria-modal', 'false');
    // And there is no legacy full-page overlay in the tree.
    expect(document.querySelector('.qb-modal-overlay')).toBeNull();
  });
});
