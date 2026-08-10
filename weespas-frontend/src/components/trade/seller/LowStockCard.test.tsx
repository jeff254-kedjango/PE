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

function out(items: ListingOut[], floor = 5): LowStockOut {
  return { floor, items };
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
  it('renders the empty state when all stock is healthy', async () => {
    mockLowStock.mockResolvedValue(out([]));
    renderCard();
    expect(await screen.findByText(/All stock healthy/i)).toBeInTheDocument();
    // No (N) counter when count is 0.
    expect(screen.queryByLabelText(/low$/i)).not.toBeInTheDocument();
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
    mockLowStock.mockResolvedValue(out([]));
    renderCard();
    await screen.findByText(/All stock healthy/i);
    fireEvent.change(screen.getByLabelText(/Low-stock threshold/i), { target: { value: '10' } });
    await waitFor(() => {
      expect(mockLowStock).toHaveBeenCalledWith(SESSION, { floor: 10 });
    });
  });

  it('shows an error state when the fetch fails', async () => {
    mockLowStock.mockRejectedValue(new Error('boom'));
    renderCard();
    expect(await screen.findByText(/Couldn.t load stock alerts/i, {}, { timeout: 3000 })).toBeInTheDocument();
  });
});
