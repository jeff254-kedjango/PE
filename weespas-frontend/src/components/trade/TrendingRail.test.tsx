import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../api/commerce')>('../../api/commerce');
  return { ...actual, getTrending: vi.fn() };
});

import { getTrending, type CommerceSession, type TrendingSlate, type TrendingProductCard } from '../../api/commerce';
import TrendingRail from './TrendingRail';

const mockGetTrending = vi.mocked(getTrending);
const SESSION: CommerceSession = { token: 'tok', commerce_url: 'http://c' };

function card(over: Partial<TrendingProductCard> = {}): TrendingProductCard {
  return {
    listing_id: 'l1', seller_id: 'sel1', title: 'Nyama Choma', price_cents: 35000, currency: 'KES',
    category: 'butchery', property_uuid: null, distance_m: 1200, boost_tier: 'mtaa',
    image_url: null,
    ...over,
  };
}

function slate(over: Partial<TrendingSlate> = {}): TrendingSlate {
  return {
    cards: [card()],
    visible_slots: 12,
    slot_seconds: 12,
    poll_seconds: 20,
    bucket: '-1.29:36.82',
    active_count: 1,
    ...over,
  };
}

function renderRail(onSelect = vi.fn(), session: CommerceSession | null = SESSION) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={qc}>
      <TrendingRail session={session} lat={-1.29} lng={36.82} onSelectSeller={onSelect} />
    </QueryClientProvider>,
  );
  return { ...utils, onSelect };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetTrending.mockResolvedValue(slate());
  // jsdom has no ResizeObserver and reports clientHeight 0, which would make useFitCount derive 1
  // slot. Stub a ResizeObserver and a tall board so the rail fits its (small) test queues — mirrors
  // a real viewport where the board has height. 800px → fits well over 12 rows (cap), so the fitted
  // count equals the queue length and every card renders, as in the browser.
  vi.stubGlobal('ResizeObserver', class {
    observe() {} unobserve() {} disconnect() {}
  });
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, value: 800 });
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete (HTMLElement.prototype as unknown as { clientHeight?: number }).clientHeight;
});

describe('TrendingRail', () => {
  it('renders boosted PRODUCT cards from the queue (title + price + boost disclosure)', async () => {
    renderRail();
    // Chunk 1 (permanent columns): the rail mounts IMMEDIATELY with a skeleton before the fetch
    // resolves — waiting on the outer chrome is no longer enough. Wait on a real card so the
    // assertions below run against the LOADED state, not the shimmer.
    await waitFor(() => expect(screen.getAllByTestId('trending-card').length).toBeGreaterThan(0));
    // The product title and its formatted price are shown.
    expect(screen.getAllByText('Nyama Choma').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('KES 350').length).toBeGreaterThanOrEqual(1);
    // The "Boosted" disclosure is no longer a visible pill (it would truncate the title); it now
    // rides the card's accessible name + the corner spark's tooltip. Assert the honesty contract
    // survives via the aria-label rather than a text node.
    const cardEl = screen.getAllByTestId('trending-card')[0];
    expect(cardEl.getAttribute('aria-label') ?? '').toContain('Boosted');
  });

  it('applies the category color to the card (different stimulus per trade)', async () => {
    renderRail();
    await waitFor(() => expect(screen.getAllByTestId('trending-card').length).toBeGreaterThan(0));
    const cardEl = screen.getAllByTestId('trending-card')[0];
    // The category color is wired through the --cat-color custom property (butchery → its token).
    expect(cardEl.getAttribute('style')).toContain('--cat-color');
    expect(cardEl.getAttribute('style')).toContain('--color-cat-butchery');
  });

  it('carries an accessible name built from title + price + category', async () => {
    renderRail();
    await waitFor(() => expect(screen.getAllByTestId('trending-card').length).toBeGreaterThan(0));
    const cardEl = screen.getAllByTestId('trending-card')[0];
    const label = cardEl.getAttribute('aria-label') ?? '';
    expect(label).toContain('Nyama Choma');
    expect(label).toContain('KES 350');
    expect(label).toContain('Butchery');
  });

  // #6 — the card's lead visual is the PRODUCT's own image when the listing has one.
  it('shows the product image as the card lead visual when present', async () => {
    mockGetTrending.mockResolvedValue(slate({
      cards: [card({ image_url: '/uploads/trade/images/loaf.webp' })],
    }));
    renderRail();
    await waitFor(() => expect(screen.getAllByTestId('trending-card').length).toBeGreaterThan(0));
    const img = screen.getByRole('img', { hidden: true });
    // resolveMediaUrl may prefix the origin, so assert the path is carried, not an exact string.
    expect(img.getAttribute('src') ?? '').toContain('/uploads/trade/images/loaf.webp');
  });

  it('falls back to the category glyph (no <img>) when the product has no image', async () => {
    // Default fixture has image_url: null.
    renderRail();
    await waitFor(() => expect(screen.getAllByTestId('trending-card').length).toBeGreaterThan(0));
    // No product <img> is rendered; the category icon (an inline svg) leads instead.
    expect(screen.queryByRole('img', { hidden: true })).toBeNull();
  });

  it('opens the storefront with the seller_id when a card is tapped', async () => {
    const { onSelect } = renderRail();
    await waitFor(() => expect(screen.getAllByTestId('trending-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('trending-card')[0]);
    expect(onSelect).toHaveBeenCalledWith('sel1');
  });

  // Chunk 1 (permanent columns): the rail STAYS MOUNTED with an honest placeholder when the
  // locality has no boosts, so the left column keeps its 248px width on load. The search toggle is
  // hidden — the search filters an already-fetched queue, so with zero items there's nothing to
  // search (rule 4: no dead affordance).
  it('stays mounted with a placeholder when the queue is empty (no boosts nearby)', async () => {
    mockGetTrending.mockResolvedValue(slate({ cards: [], active_count: 0 }));
    renderRail();
    await waitFor(() => expect(mockGetTrending).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByTestId('trending-rail-empty')).toBeInTheDocument());
    // The rail's outer chrome is present — the layout keeps its width.
    expect(screen.getByTestId('trending-rail')).toBeInTheDocument();
    // No trending cards + no search affordance.
    expect(screen.queryAllByTestId('trending-card').length).toBe(0);
    expect(screen.queryByTestId('trending-search-toggle')).toBeNull();
    // Head shows the honest "nothing trending nearby" line (not the boost count).
    expect(screen.getByText(/nothing trending nearby/i)).toBeInTheDocument();
  });

  it('does not fetch or render without a session', () => {
    renderRail(vi.fn(), null);
    expect(mockGetTrending).not.toHaveBeenCalled();
    expect(screen.queryByTestId('trending-rail')).toBeNull();
  });

  // #7 — the localized search filters the ALREADY-FETCHED queue by title, client-side, with NO
  // refetch, and its effect is scoped to this rail alone.
  it('search narrows the rail by title client-side without refetching', async () => {
    mockGetTrending.mockResolvedValue(slate({
      cards: [
        card(),
        card({ listing_id: 'l2', seller_id: 'sel2', title: 'Sourdough', category: 'bakery' }),
      ],
      active_count: 2,
    }));
    renderRail();
    await waitFor(() => expect(screen.getAllByTestId('trending-card').length).toBe(2));
    const callsBefore = mockGetTrending.mock.calls.length;

    fireEvent.click(screen.getByTestId('trending-search-toggle'));
    fireEvent.change(screen.getByTestId('trending-search-input'), { target: { value: 'sourdough' } });

    // Only the matching card survives; the query never triggered a new fetch.
    await waitFor(() => expect(screen.getAllByTestId('trending-card').length).toBe(1));
    expect(screen.getByText('Sourdough')).toBeInTheDocument();
    expect(screen.queryByText('Nyama Choma')).toBeNull();
    expect(mockGetTrending.mock.calls.length).toBe(callsBefore);
  });

  it('a search matching nothing keeps the rail mounted with a no-matches hint', async () => {
    renderRail();
    await waitFor(() => expect(screen.getAllByTestId('trending-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByTestId('trending-search-toggle'));
    fireEvent.change(screen.getByTestId('trending-search-input'), { target: { value: 'zzz-no-match' } });
    await waitFor(() => expect(screen.getByTestId('trending-search-empty')).toBeInTheDocument());
    expect(screen.getByTestId('trending-rail')).toBeInTheDocument();
    expect(screen.queryAllByTestId('trending-card').length).toBe(0);
  });

  it('closing the search restores the full queue', async () => {
    mockGetTrending.mockResolvedValue(slate({
      cards: [
        card(),
        card({ listing_id: 'l2', seller_id: 'sel2', title: 'Sourdough', category: 'bakery' }),
      ],
      active_count: 2,
    }));
    renderRail();
    await waitFor(() => expect(screen.getAllByTestId('trending-card').length).toBe(2));
    fireEvent.click(screen.getByTestId('trending-search-toggle'));  // open
    fireEvent.change(screen.getByTestId('trending-search-input'), { target: { value: 'sourdough' } });
    await waitFor(() => expect(screen.getAllByTestId('trending-card').length).toBe(1));
    fireEvent.click(screen.getByTestId('trending-search-toggle'));  // close → clears query
    await waitFor(() => expect(screen.getAllByTestId('trending-card').length).toBe(2));
    expect(screen.queryByTestId('trending-search-input')).toBeNull();
  });

  it('shows every queued product when the queue fits the slots (no cycling)', async () => {
    mockGetTrending.mockResolvedValue(slate({
      cards: [card(), card({ listing_id: 'l2', seller_id: 'sel2', title: 'Sourdough', category: 'bakery', price_cents: 20000 })],
      active_count: 2,
    }));
    renderRail();
    // active(2) <= visible_slots(12): both render, exactly once each (no duplicate-for-loop).
    await waitFor(() => expect(screen.getAllByTestId('trending-card').length).toBe(2));
    expect(screen.getByText('Nyama Choma')).toBeInTheDocument();
    expect(screen.getByText('Sourdough')).toBeInTheDocument();
  });
});
