import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ToastProvider } from '../../context/ToastContext';
import FeaturedManager from './FeaturedManager';

// --- mock the API surface this component drives ---
vi.mock('../../api/admin', () => ({
  listFeaturedProperties: vi.fn(),
  setPropertyFeatured: vi.fn(),
}));
vi.mock('../../api/properties', () => ({
  filterProperties: vi.fn(),
}));

import { listFeaturedProperties, setPropertyFeatured } from '../../api/admin';
import { filterProperties } from '../../api/properties';

const mockListFeatured = vi.mocked(listFeaturedProperties);
const mockSetFeatured = vi.mocked(setPropertyFeatured);
const mockFilter = vi.mocked(filterProperties);

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    qc,
    ...render(
      <QueryClientProvider client={qc}>
        <ToastProvider>
          <FeaturedManager token="tok" />
        </ToastProvider>
      </QueryClientProvider>,
    ),
  };
}

const page = (items: unknown[], opts: { total?: number; skip?: number } = {}) =>
  ({ total: opts.total ?? items.length, skip: opts.skip ?? 0, limit: 10, items } as never);

beforeEach(() => {
  mockListFeatured.mockResolvedValue(page([]));
  mockFilter.mockResolvedValue(page([]));
  mockSetFeatured.mockResolvedValue({ id: 'P1', is_featured: true } as never);
});
afterEach(() => { vi.clearAllMocks(); });

describe('FeaturedManager', () => {
  it('renders active promotions with a trust chip + expiry', async () => {
    mockListFeatured.mockResolvedValue(page([
      {
        id: 'P1', title: 'Certified Flat', agent_name: 'Jane',
        location_name: 'Kilimani', is_engineer_certified: true,
        featured_expires_at: new Date(Date.now() + 5 * 86_400_000 + 60_000).toISOString(),
      },
    ]));
    renderPanel();
    expect(await screen.findByText('Certified Flat')).toBeInTheDocument();
    expect(screen.getByText('Certified')).toBeInTheDocument();
    expect(screen.getByText(/Expires in 5 days/)).toBeInTheDocument();
  });

  it('Unfeature calls setPropertyFeatured(false) and refetches', async () => {
    mockListFeatured.mockResolvedValue(page([
      { id: 'P1', title: 'Flat', agent_name: 'Jane', location_name: 'X', is_featured: true },
    ]));
    renderPanel();
    const btn = await screen.findByRole('button', { name: 'Unfeature' });
    fireEvent.click(btn);
    await waitFor(() => expect(mockSetFeatured).toHaveBeenCalledWith('tok', 'P1', { is_featured: false }));
    // list refetched after the mutation
    await waitFor(() => expect(mockListFeatured).toHaveBeenCalledTimes(2));
  });

  it('search → Feature with chosen duration', async () => {
    mockFilter.mockResolvedValue(page([
      { id: 'P9', title: 'New Listing', agent_name: 'Bob', location_name: 'Y' },
    ]));
    renderPanel();
    fireEvent.change(screen.getByLabelText('Search listings to feature'), {
      target: { value: 'flat' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Search/ }));
    expect(await screen.findByText('New Listing')).toBeInTheDocument();
    // default duration is 30 days
    fireEvent.click(screen.getByRole('button', { name: 'Feature' }));
    await waitFor(() =>
      expect(mockSetFeatured).toHaveBeenCalledWith('tok', 'P9', { is_featured: true, duration_days: 30 }),
    );
  });

  it('hides already-featured listings from search results', async () => {
    mockListFeatured.mockResolvedValue(page([{ id: 'P1', title: 'Already Featured', agent_name: 'A' }]));
    mockFilter.mockResolvedValue(page([
      { id: 'P1', title: 'Already Featured', agent_name: 'A' },
      { id: 'P2', title: 'Fresh One', agent_name: 'B' },
    ]));
    renderPanel();
    // wait for active list to settle so activeIds is populated
    await screen.findByText('Already Featured');
    fireEvent.change(screen.getByLabelText('Search listings to feature'), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: /Search/ }));
    expect(await screen.findByText('Fresh One')).toBeInTheDocument();
    // "Already Featured" appears once (in active list), not duplicated in results
    expect(screen.getAllByText('Already Featured')).toHaveLength(1);
  });

  it('paginates active promotions at 10 and fetches the next page', async () => {
    const pageOne = Array.from({ length: 10 }, (_, i) => ({
      id: `A${i}`, title: `Active ${i}`, agent_name: 'X',
    }));
    const pageTwo = [{ id: 'A10', title: 'Active 10', agent_name: 'X' }];
    // total=11 → two pages; first request returns page 1, skip=10 returns page 2.
    mockListFeatured.mockImplementation((_t, params) =>
      Promise.resolve(
        (params?.skip ?? 0) >= 10 ? page(pageTwo, { total: 11, skip: 10 }) : page(pageOne, { total: 11 }),
      ),
    );
    renderPanel();
    expect(await screen.findByText('Active 0')).toBeInTheDocument();
    // First fetch asks for page 0 (skip 0, limit 10).
    expect(mockListFeatured).toHaveBeenCalledWith('tok', { skip: 0, limit: 10 });
    expect(screen.getByText('Page 1 of 2')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Next/ }));
    expect(await screen.findByText('Active 10')).toBeInTheDocument();
    expect(mockListFeatured).toHaveBeenCalledWith('tok', { skip: 10, limit: 10 });
    expect(screen.getByText('Page 2 of 2')).toBeInTheDocument();
  });

  it('shows no pager when everything fits on one page', async () => {
    mockListFeatured.mockResolvedValue(page([{ id: 'P1', title: 'Solo', agent_name: 'A' }]));
    renderPanel();
    await screen.findByText('Solo');
    expect(screen.queryByText(/Page \d+ of/)).not.toBeInTheDocument();
  });
});
