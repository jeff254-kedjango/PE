import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// The component composes several hooks; mock each so the test drives pure UI behaviour (no network,
// no react-query, no geolocation prompt). Each mock is a vi.fn we retarget per test.
const mockUseTextSearch = vi.fn();
const mockUseTradeSearch = vi.fn();
vi.mock('../../hooks/useTextSearch', () => ({ useTextSearch: () => mockUseTextSearch() }));
vi.mock('../../hooks/useTradeSearch', () => ({ useTradeSearch: () => mockUseTradeSearch() }));
vi.mock('../../hooks/useCommerceSession', () => ({
  useCommerceSession: () => ({ session: { token: 't', commerce_url: 'http://c' }, isLoading: false, error: null }),
}));
vi.mock('../../hooks/useGeolocation', () => ({
  useGeolocation: () => ({ latitude: null, longitude: null, loading: false, error: null, requestLocation: vi.fn() }),
}));
// useDebounce is identity here so the typed query takes effect synchronously.
vi.mock('../../hooks/useDebounce', () => ({ useDebounce: (v: unknown) => v }));

import NavbarSearch from './NavbarSearch';

const emptyProps = { isLoading: false, isError: false };

// The inline dropdown only renders while the box is focused, so every test focuses then types.
function renderSearch(isAuthenticated: boolean) {
  return render(
    <MemoryRouter>
      <NavbarSearch isAuthenticated={isAuthenticated} variant="inline" />
    </MemoryRouter>,
  );
}

function typeQuery(text: string) {
  const input = screen.getByLabelText('Search query');
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: text } });
}

describe('NavbarSearch — inline unified Properties + Trade search', () => {
  beforeEach(() => {
    mockUseTextSearch.mockReturnValue({ ...emptyProps, properties: [] });
    mockUseTradeSearch.mockReturnValue({ ...emptyProps, results: [] });
  });

  it('renders a property result under the Homes section with its (major-unit) price', () => {
    mockUseTextSearch.mockReturnValue({
      ...emptyProps,
      properties: [{ id: 'p1', title: 'Kilimani 2-bed', price: 45000, currency: 'KES' }],
    });
    renderSearch(true);
    typeQuery('kili');
    expect(screen.getByText('Homes · 1')).toBeTruthy();
    expect(screen.getByText('Kilimani 2-bed')).toBeTruthy();
    // 45000 major units → the utils/format compact form.
    expect(screen.getByText('KES 45K')).toBeTruthy();
  });

  it('shows BOTH sections at once when properties and trade results co-exist', () => {
    mockUseTextSearch.mockReturnValue({
      ...emptyProps,
      properties: [{ id: 'p1', title: 'Kilimani 2-bed', price: 45000, currency: 'KES' }],
    });
    mockUseTradeSearch.mockReturnValue({
      ...emptyProps,
      results: [{
        listing_id: 'l1', seller_id: 's1', shop_id: 'sh1', shop_name: 'Mama Mboga',
        shop_category: 'greengrocer', title: 'Sukuma bunch', price_cents: 250000, currency: 'KES',
        image_url: null, media_urls: [], property_uuid: null, distance_m: 120,
      }],
    });
    renderSearch(true);
    typeQuery('xy'); // ≥ MIN_QUERY_LEN so both sections render
    expect(screen.getByText('Homes · 1')).toBeTruthy();
    expect(screen.getByText('Shops & Products · 1')).toBeTruthy();
  });

  it('formats a trade result price from MINOR units (cents), not raw', () => {
    mockUseTradeSearch.mockReturnValue({
      ...emptyProps,
      results: [{
        listing_id: 'l1', seller_id: 's1', shop_id: 'sh1', shop_name: 'Mama Mboga',
        shop_category: 'greengrocer', title: 'Sukuma bunch', price_cents: 250000, currency: 'KES',
        image_url: null, media_urls: [], property_uuid: null, distance_m: 120,
      }],
    });
    renderSearch(true);
    typeQuery('sukuma');
    expect(screen.getByText('Sukuma bunch')).toBeTruthy();
    // 250000 cents = KES 2,500 — the commerce formatter divides by 100. A raw render would show
    // "KES 250,000" (the bug this test pins).
    expect(screen.getByText('KES 2,500')).toBeTruthy();
    expect(screen.queryByText('KES 250,000')).toBeNull();
  });

  it('hides the Shops & Products section for an anonymous user (commerce needs a session)', () => {
    mockUseTextSearch.mockReturnValue({
      ...emptyProps,
      properties: [{ id: 'p1', title: 'Public home', price: 30000, currency: 'KES' }],
    });
    // Even if the trade hook somehow returned rows, the anon path must not render them.
    mockUseTradeSearch.mockReturnValue({
      ...emptyProps,
      results: [{
        listing_id: 'l1', seller_id: 's1', shop_id: 'sh1', shop_name: 'Mama Mboga',
        shop_category: 'greengrocer', title: 'Should not show', price_cents: 100, currency: 'KES',
        image_url: null, media_urls: [], property_uuid: null, distance_m: 10,
      }],
    });
    renderSearch(false);
    typeQuery('anything');
    expect(screen.getByText('Public home')).toBeTruthy();
    expect(screen.queryByText(/Shops & Products/)).toBeNull();
    expect(screen.queryByText('Should not show')).toBeNull();
  });

  it('shows the intro hint (no result sections) until the query meets the minimum length', () => {
    renderSearch(true);
    typeQuery('a'); // 1 char — below MIN_QUERY_LEN
    expect(screen.getByText('Find homes, shops and products near you.')).toBeTruthy();
    expect(screen.queryByText(/Homes ·/)).toBeNull();
  });

  it('uses the "Search Houses, Shops, Products…" placeholder', () => {
    renderSearch(true);
    expect(screen.getByPlaceholderText('Search Houses, Shops, Products…')).toBeTruthy();
  });
});
