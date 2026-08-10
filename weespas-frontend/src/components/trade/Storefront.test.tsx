import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../api/commerce')>('../../api/commerce');
  return {
    ...actual,
    getPublicStorefront: vi.fn(),
    getPublicStorefrontByHandle: vi.fn(),
    getShopProfile: vi.fn(),
    toggleShopFollow: vi.fn(),
    getSellerReviews: vi.fn(),
  };
});

import {
  getPublicStorefront, getPublicStorefrontByHandle, getShopProfile,
  toggleShopFollow, getSellerReviews,
  type CommerceSession, type PublicStorefront, type ShopProfile,
  type SellerReviewsPage,
} from '../../api/commerce';
import Storefront from './Storefront';

const mockStorefront = vi.mocked(getPublicStorefront);
const mockStorefrontByHandle = vi.mocked(getPublicStorefrontByHandle);
const mockProfile = vi.mocked(getShopProfile);
const mockToggleFollow = vi.mocked(toggleShopFollow);
const mockReviews = vi.mocked(getSellerReviews);

const SESSION: CommerceSession = { token: 'tok', commerce_url: 'http://c' };

function storefront(over: Partial<PublicStorefront> = {}): PublicStorefront {
  return {
    seller_id: 'sel1',
    display_name: 'Njeri',
    rating: 4.5,
    review_count: 12,
    shops: [{
      shop: {
        id: 'shop1', seller_id: 'sel1', name: 'Mama Njeri Groceries', handle: null,
        property_uuid: null, lat: -1.29, lng: 36.82, created_at: '2026-06-29T00:00:00Z',
      },
      listings: [
        { id: 'l1', shop_id: 'shop1', seller_id: 'sel1', property_uuid: null,
          title: 'Fresh tomatoes', price_cents: 5000, currency: 'KES',
          media_urls: ['/uploads/trade/images/tom.jpg'],
          pricing_mode: 'fixed', created_at: '2026-06-29T00:00:00Z' },
        { id: 'l2', shop_id: 'shop1', seller_id: 'sel1', property_uuid: null,
          title: 'Onions', price_cents: 3000, currency: 'KES',
          media_urls: [],
          pricing_mode: 'bargain', created_at: '2026-06-29T00:00:00Z' },
      ],
    }],
    ...over,
  };
}

function profile(over: Partial<ShopProfile> = {}): ShopProfile {
  return {
    shop_id: 'shop1', seller_id: 'sel1', name: 'Mama Njeri Groceries',
    avatar_url: null, banner_url: null, description: null, contact: null,
    category: 'greengrocer', property_uuid: null,
    follower_count: 4, following: false, rating: 4.5, review_count: 12,
    ...over,
  };
}

function reviewsPage(over: Partial<SellerReviewsPage> = {}): SellerReviewsPage {
  return {
    summary: { average: 4.5, count: 12 },
    items: [
      { id: 'r1', order_id: 'o1', seller_id: 'sel1', listing_id: 'l1',
        reviewer_uuid: 'u1', rating: 5, body: 'Great produce', created_at: '2026-06-28T00:00:00Z' },
      { id: 'r2', order_id: 'o2', seller_id: 'sel1', listing_id: 'l2',
        reviewer_uuid: 'u2', rating: 4, body: null, created_at: '2026-06-27T00:00:00Z' },
    ],
    next_cursor: null,
    ...over,
  };
}

function renderIt(props: Partial<React.ComponentProps<typeof Storefront>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Storefront
        session={SESSION}
        entry={{ sellerId: 'sel1' }}
        {...props}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockStorefront.mockResolvedValue(storefront());
  mockStorefrontByHandle.mockResolvedValue(storefront());
  mockProfile.mockResolvedValue(profile());
  mockReviews.mockResolvedValue(reviewsPage());
});

describe('Storefront — resolution & entry', () => {
  it('by-sellerId entry: calls getPublicStorefront (not the handle variant)', async () => {
    renderIt({ entry: { sellerId: 'sel1' } });
    await waitFor(() => expect(mockStorefront).toHaveBeenCalledWith(SESSION, 'sel1'));
    expect(mockStorefrontByHandle).not.toHaveBeenCalled();
  });

  it('by-handle entry: calls getPublicStorefrontByHandle with a lowercased handle', async () => {
    renderIt({ entry: { handle: 'Mama-Njeri' } });
    // The hook lowers the key so mixed-case URLs share cache; the query fn receives the same.
    await waitFor(() => expect(mockStorefrontByHandle).toHaveBeenCalledWith(SESSION, 'mama-njeri'));
    expect(mockStorefront).not.toHaveBeenCalled();
  });

  it('renders the loading skeleton before data resolves', () => {
    // A promise that never resolves so we can inspect the pending state.
    mockStorefront.mockReturnValue(new Promise(() => {}));
    renderIt();
    expect(screen.getByTestId('storefront-loading')).toBeInTheDocument();
  });

  it('renders an error state when the fetch fails', async () => {
    // useStorefront sets retry: 1 (hook-level, wins over the client default), so a persistent
    // failure needs both attempts to reject before React Query surfaces `isError`. Waiting long
    // enough for the second attempt is enough — no need to fight the retry policy in the test.
    mockStorefront.mockRejectedValue(new Error('boom'));
    renderIt();
    await waitFor(
      () => expect(screen.getByTestId('storefront-error')).toBeInTheDocument(),
      { timeout: 3000 },
    );
    expect(mockStorefront).toHaveBeenCalledTimes(2);
  });
});

describe('Storefront — identity header', () => {
  it('shows the shop name, category badge, rating and follower count once profile lands', async () => {
    renderIt();
    await waitFor(() => expect(screen.getByTestId('storefront-name')).toHaveTextContent('Mama Njeri Groceries'));
    // Rating aggregate from the storefront DTO.
    expect(screen.getByTestId('storefront-rating').textContent).toMatch(/4\.5.*12 reviews/);
    // Category + followers wait on the lazy profile fetch.
    await waitFor(() => expect(screen.getByTestId('storefront-category')).toHaveTextContent(/greengrocer/i));
    await waitFor(() => expect(screen.getByTestId('storefront-followers').textContent).toMatch(/4 followers/));
  });

  it('shows "No ratings yet" when the seller is unrated', async () => {
    mockStorefront.mockResolvedValue(storefront({ rating: null, review_count: 0 }));
    renderIt();
    await waitFor(() => expect(screen.getByTestId('storefront-rating')).toHaveTextContent(/No ratings yet/));
  });
});

describe('Storefront — follow toggle', () => {
  it('renders the Follow button once profile loads (with a session)', async () => {
    renderIt();
    await waitFor(() => expect(screen.getByTestId('storefront-follow')).toHaveTextContent(/Follow/));
    // Not yet "Following".
    expect(screen.getByTestId('storefront-follow').getAttribute('aria-pressed')).toBe('false');
  });

  it('does NOT render the Follow button when there is no session (anonymous view)', async () => {
    renderIt({ session: null });
    // Storefront fetch is gated on session too — without it the fetch never fires, so the
    // component stays in loading. That's the correct behavior: an anonymous viewer can't
    // reach here without a session in the current wiring. Verify no follow button leaked.
    expect(screen.queryByTestId('storefront-follow')).toBeNull();
  });

  it('optimistically flips to Following + bumps count before the server responds', async () => {
    // The mutation is held pending so we can observe the OPTIMISTIC state.
    let resolveToggle!: (v: { shop_id: string; following: boolean; follower_count: number }) => void;
    mockToggleFollow.mockReturnValue(new Promise((r) => { resolveToggle = r; }));

    renderIt();
    await waitFor(() => expect(screen.getByTestId('storefront-follow')).toBeInTheDocument());
    // Baseline: 4 followers, not following.
    expect(screen.getByTestId('storefront-followers').textContent).toMatch(/4 followers/);

    fireEvent.click(screen.getByTestId('storefront-follow'));
    // Optimistic: 5 followers + label flips before the promise resolves.
    await waitFor(() => expect(screen.getByTestId('storefront-followers').textContent).toMatch(/5 followers/));
    expect(screen.getByTestId('storefront-follow')).toHaveTextContent(/Following/);

    // Server confirms with its own numbers (which may differ — here 6 to prove server wins).
    resolveToggle({ shop_id: 'shop1', following: true, follower_count: 6 });
    await waitFor(() => expect(screen.getByTestId('storefront-followers').textContent).toMatch(/6 followers/));
  });

  it('rolls back the optimistic flip on server error', async () => {
    mockToggleFollow.mockRejectedValue(new Error('nope'));
    renderIt();
    await waitFor(() => expect(screen.getByTestId('storefront-follow')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('storefront-follow'));
    // After the rejection settles, the count is back to 4 and the label is Follow again.
    await waitFor(() => expect(screen.getByTestId('storefront-follow')).toHaveTextContent(/Follow$/));
    expect(screen.getByTestId('storefront-followers').textContent).toMatch(/4 followers/);
  });
});

describe('Storefront — catalogue grid', () => {
  it('renders one card per listing with title + formatted price', async () => {
    renderIt();
    await waitFor(() => expect(screen.getByTestId('storefront-catalogue')).toBeInTheDocument());
    const cards = screen.getAllByTestId('storefront-card');
    expect(cards).toHaveLength(2);
    expect(cards[0].textContent).toMatch(/Fresh tomatoes/);
    // formatPrice renders KES 5000 cents as the currency-formatted amount; we just assert the
    // number-y part is present rather than pinning the exact locale glyph.
    expect(cards[0].textContent).toMatch(/50/);
    // Bargain listings are labelled so a buyer knows the price is negotiable.
    expect(cards[1].textContent).toMatch(/Bargain/);
  });

  it('shows the empty state when the shop has no listings', async () => {
    mockStorefront.mockResolvedValue(storefront({
      shops: [{
        shop: {
          id: 'shop1', seller_id: 'sel1', name: 'Mama Njeri Groceries', handle: null,
          property_uuid: null, lat: -1.29, lng: 36.82, created_at: '2026-06-29T00:00:00Z',
        },
        listings: [],
      }],
    }));
    renderIt();
    await waitFor(() => expect(screen.getByTestId('storefront-catalogue-empty')).toBeInTheDocument());
  });

  it('cards are interactive ONLY when onSelectListing is provided', async () => {
    const onSelect = vi.fn();
    const { unmount } = renderIt({ onSelectListing: onSelect });
    await waitFor(() => expect(screen.getByTestId('storefront-catalogue')).toBeInTheDocument());
    const card = screen.getAllByTestId('storefront-card')[0];
    // Interactive variant is a <button>.
    expect(card.tagName).toBe('BUTTON');
    fireEvent.click(card);
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0].id).toBe('l1');
    unmount();

    // Static variant — no onSelect: renders as a <div>, click does nothing.
    renderIt();
    await waitFor(() => expect(screen.getByTestId('storefront-catalogue')).toBeInTheDocument());
    const staticCard = screen.getAllByTestId('storefront-card')[0];
    expect(staticCard.tagName).toBe('DIV');
  });
});

describe('Storefront — reviews tab', () => {
  it('does NOT fetch reviews until the buyer switches to the reviews tab', async () => {
    renderIt();
    await waitFor(() => expect(screen.getByTestId('storefront-catalogue')).toBeInTheDocument());
    expect(mockReviews).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('storefront-tab-reviews'));
    await waitFor(() => expect(mockReviews).toHaveBeenCalledTimes(1));
    expect(mockReviews.mock.calls[0][1]).toBe('sel1'); // keyed by seller_id, not shop_id
  });

  it('renders review rows with stars + body', async () => {
    renderIt();
    await waitFor(() => expect(screen.getByTestId('storefront-tab-reviews')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('storefront-tab-reviews'));
    await waitFor(() => expect(screen.getByTestId('storefront-reviews')).toBeInTheDocument());
    const rows = screen.getAllByTestId('storefront-review');
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toMatch(/Great produce/);
    // Star row is filled to the rating; screen-reader label carries the numeric.
    expect(rows[0].querySelector('.storefront__review-stars')?.getAttribute('aria-label')).toMatch(/5 out of 5/);
    // Second review has no body — rendered as stars-only.
    expect(rows[1].querySelector('.storefront__review-body')).toBeNull();
  });

  it('shows the empty state when the seller has no reviews', async () => {
    mockReviews.mockResolvedValue({
      summary: { average: null, count: 0 }, items: [], next_cursor: null,
    });
    renderIt();
    await waitFor(() => expect(screen.getByTestId('storefront-tab-reviews')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('storefront-tab-reviews'));
    await waitFor(() => expect(screen.getByTestId('storefront-reviews-empty')).toBeInTheDocument());
  });
});

describe('Storefront — page-only', () => {
  // Chunk A: the sheet mount is gone. The Storefront always renders as a page, and there is no
  // in-component close button — navigation is handled by the parent (ShopPage's "Back to Trade"
  // link, or the browser back button when arriving via a route push from /trade).
  it('does NOT render an in-component close button', async () => {
    renderIt();
    await waitFor(() => expect(screen.getByTestId('storefront')).toBeInTheDocument());
    expect(screen.queryByLabelText('Close')).toBeNull();
  });
});
