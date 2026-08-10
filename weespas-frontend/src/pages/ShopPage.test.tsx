// ShopPage routing tests — the /shop/:key single-slot route (§8):
//   * "@<handle>" → canonical handle page.
//   * bare sellerId → legacy page; canonicalizes (Navigate replace) to /shop/@<handle> when the
//     resolved storefront reports one.
// The Storefront component is mocked to a bare marker so these tests focus on ROUTING; the
// storefront's own rendering is covered by Storefront.test.tsx. The by-sellerId path still needs
// a real network answer to decide the redirect, so we mock api/commerce (NOT the useStorefront
// hook — the hook is the code we want to exercise).
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

vi.mock('../hooks/useCommerceSession', () => ({
  useCommerceSession: () => ({ session: { token: 'tok', commerce_url: 'http://c' }, isLoading: false, error: null }),
}));

vi.mock('../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../api/commerce')>('../api/commerce');
  return {
    ...actual,
    getPublicStorefront: vi.fn(),
    getPublicStorefrontByHandle: vi.fn(),
    // Storefront itself calls these under the hood — stub them so the mocked Storefront can
    // render without hitting anything.
    getShopProfile: vi.fn(),
    getSellerReviews: vi.fn(),
  };
});

// Storefront is rendered on both branches; mock it to a marker that records what entry it
// received so we can assert routing without pulling in the whole component tree. Chunk A: no
// more mount prop — Storefront is page-only, so there's just one variant.
vi.mock('../components/trade/Storefront', () => ({
  default: (props: { entry: { sellerId?: string; handle?: string } }) => (
    <div
      data-testid="storefront-mock"
      data-entry={JSON.stringify(props.entry)}
    />
  ),
}));

import { getPublicStorefront, type PublicStorefront } from '../api/commerce';
import ShopPage from './ShopPage';

const mockStorefront = vi.mocked(getPublicStorefront);

function storefront(over: Partial<PublicStorefront> = {}): PublicStorefront {
  return {
    seller_id: 'sel1', display_name: 'Njeri', rating: null, review_count: 0,
    shops: [{
      shop: {
        id: 'shop1', seller_id: 'sel1', name: 'Mama Njeri', handle: null,
        property_uuid: null, lat: -1.29, lng: 36.82, created_at: '2026-06-29T00:00:00Z',
      },
      listings: [],
    }],
    ...over,
  };
}

function renderAt(url: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/shop/:key" element={<ShopPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ShopPage — /shop/@<handle>', () => {
  it('mounts Storefront with the handle entry (stripped of the "@" prefix)', async () => {
    renderAt('/shop/@mama-mboga');
    await waitFor(() => expect(screen.getByTestId('storefront-mock')).toBeInTheDocument());
    const marker = screen.getByTestId('storefront-mock');
    expect(JSON.parse(marker.getAttribute('data-entry')!)).toEqual({ handle: 'mama-mboga' });
  });

  it('renders a "Back to Trade" link that navigates to /trade', async () => {
    renderAt('/shop/@mama-mboga');
    const back = await screen.findByTestId('shop-page-back');
    // A shared-URL arrival (no history) must still have a way HOME — <Link to="/trade"> not
    // navigate(-1). We assert the href, not a click behaviour, because MemoryRouter's back
    // stack is empty in this test.
    expect(back.tagName).toBe('A');
    expect(back.getAttribute('href')).toBe('/trade');
    expect(back.textContent).toMatch(/Back to Trade/);
  });
});

describe('ShopPage — /shop/<sellerId> canonical redirect', () => {
  it('renders in place when the resolved shop has NO handle (legacy shareable path)', async () => {
    mockStorefront.mockResolvedValue(storefront()); // handle: null
    renderAt('/shop/sel1');
    await waitFor(() => expect(screen.getByTestId('storefront-mock')).toBeInTheDocument());
    const marker = screen.getByTestId('storefront-mock');
    expect(JSON.parse(marker.getAttribute('data-entry')!)).toEqual({ sellerId: 'sel1' });
    // Legacy-URL viewers still need the escape hatch to /trade.
    expect(screen.getByTestId('shop-page-back').getAttribute('href')).toBe('/trade');
  });

  it('redirects to /shop/@<handle> once the storefront resolves with a handle', async () => {
    // The shop resolves with a handle → ShopPage observes it and Navigate-replaces to the
    // canonical URL. The router then re-mounts ShopPage under /shop/@mama-mboga, which reads
    // "@mama-mboga" and passes { handle: 'mama-mboga' } to Storefront.
    mockStorefront.mockResolvedValue(storefront({
      shops: [{
        shop: {
          id: 'shop1', seller_id: 'sel1', name: 'Mama Njeri', handle: 'mama-mboga',
          property_uuid: null, lat: -1.29, lng: 36.82, created_at: '2026-06-29T00:00:00Z',
        },
        listings: [],
      }],
    }));
    renderAt('/shop/sel1');
    await waitFor(() => {
      const marker = screen.getByTestId('storefront-mock');
      expect(JSON.parse(marker.getAttribute('data-entry')!)).toEqual({ handle: 'mama-mboga' });
    });
  });
});
