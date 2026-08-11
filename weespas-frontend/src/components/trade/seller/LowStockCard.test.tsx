import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../../api/commerce')>('../../../api/commerce');
  return { ...actual, getMyLowStock: vi.fn() };
});

import {
  getMyLowStock,
  type CommerceSession,
  type ListingOut,
  type LowStockOut,
} from '../../../api/commerce';
import LowStockCard from './LowStockCard';

const mockLowStock = vi.mocked(getMyLowStock);
const SESSION: CommerceSession = { token: 'ctok', commerce_url: 'http://c' };

function listing(overrides: Partial<ListingOut> = {}): ListingOut {
  // We only touch the fields LowStockCard renders (id, title, stock_qty, is_out_of_stock).
  // The rest is padded with plausible defaults via `as unknown as ListingOut` — the schema
  // has ~30 fields and the test doesn't need them to be accurate; the render code doesn't
  // read them.
  return {
    id: 'lst-1',
    title: 'Kikoi tote bag',
    stock_qty: 1,
    is_out_of_stock: false,
    is_low_stock: true,
    is_active: true,
    media_urls: [],
    ...overrides,
  } as unknown as ListingOut;
}

/** Single-shop response — the common case, so headers stay hidden. */
function out(items: ListingOut[], floor = 5): LowStockOut {
  return { floor, groups: [{ shop_id: 'shop-1', shop_name: 'Mama Mboga', items }] };
}

/** Multi-shop response, built from [shopName, items] pairs. */
function grouped(pairs: [string, ListingOut[]][], floor = 5): LowStockOut {
  return {
    floor,
    groups: pairs.map(([shop_name, items], i) => ({ shop_id: `shop-${i}`, shop_name, items })),
  };
}

function renderCard(onRestock?: (li: ListingOut) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <LowStockCard session={SESSION} onRestock={onRestock} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('LowStockCard', () => {
  it('renders the empty state naming the active threshold', async () => {
    // A seller with no low stock gets no groups at all, not an empty group.
    mockLowStock.mockResolvedValue({ floor: 5, groups: [] });
    renderCard();
    // The message states the threshold, so an empty list reads as "nothing at or below 5"
    // rather than an ambiguous "all healthy" that hides which threshold produced it.
    expect(await screen.findByText(/Nothing at or below 5/i)).toBeInTheDocument();
    // No (N) counter when count is 0.
    expect(screen.queryByLabelText(/low$/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId('low-stock-scroll')).not.toBeInTheDocument();
  });

  it('lists each low-stock item with its title + qty', async () => {
    mockLowStock.mockResolvedValue(out([
      listing({ id: 'a', title: 'Kikoi tote bag', stock_qty: 1 }),
      listing({ id: 'b', title: 'Maize flour 2kg', stock_qty: 3 }),
    ]));
    renderCard();
    const rows = await screen.findAllByTestId('low-stock-row');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent(/Kikoi tote bag/);
    expect(rows[0]).toHaveTextContent(/1 left/);
    expect(rows[1]).toHaveTextContent(/Maize flour 2kg/);
    expect(rows[1]).toHaveTextContent(/3 left/);
  });

  it('shows the (N) counter next to the header', async () => {
    mockLowStock.mockResolvedValue(out([listing({ id: 'a' }), listing({ id: 'b' }), listing({ id: 'c' })]));
    renderCard();
    expect(await screen.findByLabelText(/3 low/i)).toBeInTheDocument();
  });

  it('renders "Out of stock" instead of "0 left" for stock_qty=0', async () => {
    mockLowStock.mockResolvedValue(out([listing({ id: 'a', stock_qty: 0, is_out_of_stock: true })]));
    renderCard();
    await screen.findByTestId('low-stock-row');
    expect(screen.getByText(/Out of stock/i)).toBeInTheDocument();
  });

  it('fires onRestock with the listing when the button is clicked', async () => {
    const li = listing({ id: 'lst-42' });
    mockLowStock.mockResolvedValue(out([li]));
    const onRestock = vi.fn();
    renderCard(onRestock);
    await screen.findByTestId('low-stock-row');
    fireEvent.click(screen.getByTestId('low-stock-restock'));
    expect(onRestock).toHaveBeenCalledWith(li);
  });

  it('omits the Restock button when onRestock is not supplied', async () => {
    mockLowStock.mockResolvedValue(out([listing({ id: 'a' })]));
    renderCard();
    await screen.findByTestId('low-stock-row');
    expect(screen.queryByTestId('low-stock-restock')).not.toBeInTheDocument();
  });

  it('re-fetches with a new floor when the threshold input changes', async () => {
    mockLowStock.mockResolvedValue({ floor: 5, groups: [] });
    renderCard();
    await screen.findByText(/Nothing at or below 5/i);
    fireEvent.change(screen.getByLabelText(/Low-stock threshold/i), { target: { value: '10' } });
    await waitFor(() => {
      expect(mockLowStock).toHaveBeenCalledWith(SESSION, { floor: 10 });
    });
  });

  it('keeps the last committed floor while the input is cleared mid-edit', async () => {
    // Typing "12" over "5" requires passing through "" — that must not refetch at floor 0 or
    // snap the field back to "0" under the seller's cursor.
    mockLowStock.mockResolvedValue({ floor: 5, groups: [] });
    renderCard();
    await screen.findByText(/Nothing at or below 5/i);
    const input = screen.getByLabelText(/Low-stock threshold/i) as HTMLInputElement;

    fireEvent.change(input, { target: { value: '' } });
    expect(input.value).toBe('');                     // field stays empty for the typist
    expect(mockLowStock).not.toHaveBeenCalledWith(SESSION, { floor: 0 });

    fireEvent.change(input, { target: { value: '12' } });
    await waitFor(() => expect(mockLowStock).toHaveBeenCalledWith(SESSION, { floor: 12 }));
  });

  it('snaps an out-of-range or empty draft back to the committed floor on blur', async () => {
    mockLowStock.mockResolvedValue({ floor: 5, groups: [] });
    renderCard();
    await screen.findByText(/Nothing at or below 5/i);
    const input = screen.getByLabelText(/Low-stock threshold/i) as HTMLInputElement;

    // 999 exceeds MAX_FLOOR, so it is never committed; the field must not keep displaying a
    // number the list doesn't reflect.
    fireEvent.change(input, { target: { value: '999' } });
    fireEvent.blur(input);
    expect(input.value).toBe('5');
    expect(mockLowStock).not.toHaveBeenCalledWith(SESSION, { floor: 999 });
  });

  it('groups rows under a per-shop header when the seller has several shops', async () => {
    mockLowStock.mockResolvedValue(grouped([
      ['Juja Grocers', [listing({ id: 'a', title: 'Kikoi tote bag', stock_qty: 1 })]],
      ['Kilimani Kiosk', [
        listing({ id: 'b', title: 'Mango kilo', stock_qty: 0, is_out_of_stock: true }),
        listing({ id: 'c', title: 'Maize flour 2kg', stock_qty: 3 }),
      ]],
    ]));
    renderCard();
    const headers = await screen.findAllByTestId('low-stock-shop-header');
    expect(headers.map((h) => h.textContent)).toEqual(['Juja Grocers', 'Kilimani Kiosk']);
    // The (N) counter sums across shops, since the server no longer sends a flat total.
    expect(screen.getByLabelText(/3 low/i)).toBeInTheDocument();
    // Rows keep server order: shop by shop, most-urgent-first within each.
    const rows = screen.getAllByTestId('low-stock-row');
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('Kikoi tote bag'),
      expect.stringContaining('Mango kilo'),
      expect.stringContaining('Maize flour 2kg'),
    ]);
  });

  it('omits shop headers for a single-shop seller', async () => {
    // One shop needs no disambiguation — a header would just repeat what the seller knows.
    mockLowStock.mockResolvedValue(out([listing({ id: 'a' })]));
    renderCard();
    await screen.findByTestId('low-stock-row');
    expect(screen.queryByTestId('low-stock-shop-header')).not.toBeInTheDocument();
  });

  it('puts the rows in a keyboard-scrollable region so a long list stays inside the card', async () => {
    mockLowStock.mockResolvedValue(out([listing({ id: 'a' }), listing({ id: 'b' })]));
    renderCard();
    const region = await screen.findByTestId('low-stock-scroll');
    // The 60vh cap lives in CSS; what the component must guarantee is that the region exists
    // and is reachable by keyboard (a plain overflow div is not focusable).
    expect(region).toHaveAttribute('tabindex', '0');
    expect(region).toHaveAttribute('aria-label', 'Low-stock listings');
    expect(region.querySelectorAll('[data-testid="low-stock-row"]')).toHaveLength(2);
  });

  it('shows an error state when the fetch fails', async () => {
    mockLowStock.mockRejectedValue(new Error('boom'));
    renderCard();
    expect(await screen.findByText(/Couldn.t load stock alerts/i, {}, { timeout: 3000 })).toBeInTheDocument();
  });
});
