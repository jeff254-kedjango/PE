import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// Mock the commerce surface; keep pure helpers (majorToCents etc.) real.
vi.mock('../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../api/commerce')>('../../api/commerce');
  return {
    ...actual,
    getMyStorefront: vi.fn(),
    createPost: vi.fn(),
    createListing: vi.fn(),
    uploadTradeMedia: vi.fn(),
  };
});

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../context/ToastContext', () => ({ useToast: () => ({ toast }) }));

import {
  getMyStorefront, createPost, createListing,
  type CommerceSession, type StorefrontOut, type ListingOut, type StorefrontShop,
} from '../../api/commerce';
import ComposerBox from './ComposerBox';

const mockStorefront = vi.mocked(getMyStorefront);
const mockCreatePost = vi.mocked(createPost);
const mockCreateListing = vi.mocked(createListing);

const SESSION: CommerceSession = { token: 'ctok', commerce_url: 'http://c' };

const SHOP: StorefrontShop = {
  shop: { id: 'shop1', seller_id: 'sel1', name: 'Mama Njeri', avatar_url: null, banner_url: null, handle: null, property_uuid: null, lat: -1.29, lng: 36.82, created_at: '2026-06-29T00:00:00Z' },
  listings: [],
};

function storefront(shops: StorefrontShop[]): StorefrontOut {
  return { seller_id: 'sel1', display_name: 'Mama', rating: null, review_count: 0, shops };
}

function makeListingOut(): ListingOut {
  return {
    id: 'l1', shop_id: 'shop1', seller_id: 'sel1', property_uuid: null, title: 'X', description: null,
    price_cents: 0, currency: 'KES', media_urls: [], intent_weight: 1, is_active: true,
    stock_qty: 0, low_stock_threshold: 0, pricing_mode: 'fixed', is_short_video: false, post_kind: 'product',
    is_out_of_stock: true, is_low_stock: false, promo_mode: null, promo_started_at: null,
    promo_expires_at: null, is_promoted: false,
    flash_price_cents: null, flash_started_at: null, flash_expires_at: null, flash_reference_cents: null, is_flash_active: false,
    created_at: '2026-06-29T00:00:00Z',
  };
}

function renderComposer(shops: StorefrontShop[] = [SHOP]) {
  mockStorefront.mockResolvedValue(storefront(shops));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ComposerBox session={SESSION} weespasToken="wtok" lat={-1.29} lng={36.82} authorName="Mama" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockCreatePost.mockResolvedValue(makeListingOut());
  mockCreateListing.mockResolvedValue(makeListingOut());
});

describe('ComposerBox', () => {
  it('starts collapsed and expands on click', () => {
    renderComposer();
    expect(screen.getByTestId('composer-open')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('composer-open'));
    expect(screen.getByTestId('composer')).toBeInTheDocument();
    expect(screen.getByTestId('composer-body')).toBeInTheDocument();
  });

  it('Post mode publishes a plain post with the body + location', async () => {
    renderComposer();
    fireEvent.click(screen.getByTestId('composer-open'));
    fireEvent.change(screen.getByTestId('composer-body'), { target: { value: 'Hello street!' } });
    fireEvent.click(screen.getByTestId('composer-submit'));
    await waitFor(() => expect(mockCreatePost).toHaveBeenCalledTimes(1));
    const body = mockCreatePost.mock.calls[0][1];
    expect(body.body).toBe('Hello street!');
    expect(body.lat).toBe(-1.29);
    expect(body.lng).toBe(36.82);
    // A plain post never creates a product listing.
    expect(mockCreateListing).not.toHaveBeenCalled();
  });

  it('inserts an emoji into the post body at the caret', () => {
    renderComposer();
    fireEvent.click(screen.getByTestId('composer-open'));
    const body = screen.getByTestId('composer-body') as HTMLTextAreaElement;
    fireEvent.change(body, { target: { value: 'hi ' } });
    fireEvent.click(screen.getByTestId('composer-emoji'));
    // Pick the first emoji from the palette.
    const first = screen.getAllByRole('button').find((b) => b.getAttribute('aria-label')?.startsWith('Insert'));
    fireEvent.click(first!);
    expect((screen.getByTestId('composer-body') as HTMLTextAreaElement).value.length).toBeGreaterThan('hi '.length);
  });

  it('Product mode reveals product fields and publishes a listing', async () => {
    renderComposer();
    fireEvent.click(screen.getByTestId('composer-open'));
    fireEvent.click(screen.getByTestId('composer-mode-product'));
    await waitFor(() => expect(screen.getByTestId('listing-description')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Fresh sukuma' } });
    fireEvent.change(screen.getByLabelText('Price (KES)'), { target: { value: '150' } });
    fireEvent.click(screen.getByTestId('composer-submit'));
    await waitFor(() => expect(mockCreateListing).toHaveBeenCalledTimes(1));
    expect(mockCreateListing.mock.calls[0][2].price_cents).toBe(15000);
    expect(mockCreatePost).not.toHaveBeenCalled();
  });

  it('Product mode without a shop shows the CTA and never publishes', async () => {
    renderComposer([]); // seller has no shop
    fireEvent.click(screen.getByTestId('composer-open'));
    fireEvent.click(screen.getByTestId('composer-mode-product'));
    await waitFor(() => expect(screen.getByTestId('composer-no-shop')).toBeInTheDocument());
    // The submit button is disabled (no valid product), and there is no product form.
    expect(screen.queryByTestId('listing-description')).toBeNull();
    expect((screen.getByTestId('composer-submit') as HTMLButtonElement).disabled).toBe(true);
    expect(mockCreateListing).not.toHaveBeenCalled();
  });

  // ── The composer tool row ────────────────────────────────────────────────────────────────────
  describe('tool row', () => {
    it('shows all six tools while collapsed', () => {
      renderComposer();
      for (const key of ['write', 'sell', 'video', 'poll', 'pictures', 'audio']) {
        expect(screen.getByTestId(`composer-tool-${key}`)).toBeInTheDocument();
      }
    });

    it('"Write Post" expands straight into Post mode', () => {
      renderComposer();
      fireEvent.click(screen.getByTestId('composer-tool-write'));
      expect(screen.getByTestId('composer-body')).toBeInTheDocument();
      expect(screen.getByTestId('composer-mode-post').getAttribute('aria-selected')).toBe('true');
    });

    it('"Sell Product" expands straight into Product mode (no second click)', async () => {
      renderComposer();
      fireEvent.click(screen.getByTestId('composer-tool-sell'));
      expect(screen.getByTestId('composer-mode-product').getAttribute('aria-selected')).toBe('true');
      await waitFor(() => expect(screen.getByTestId('listing-description')).toBeInTheDocument());
    });

    // The gesture-critical path: the file <input> only exists once the composer is expanded, so the
    // handler must commit the expansion synchronously (flushSync) and click the input inside the SAME
    // user gesture — otherwise the browser suppresses the dialog. Asserting the click lands proves
    // the input was mounted in time.
    it('"Post Pictures" opens the image picker in the same gesture', () => {
      renderComposer();
      const clicks: string[] = [];
      const spy = vi.spyOn(HTMLInputElement.prototype, 'click')
        .mockImplementation(function (this: HTMLInputElement) { clicks.push(this.accept); });
      try {
        fireEvent.click(screen.getByTestId('composer-tool-pictures'));
      } finally { spy.mockRestore(); }
      expect(clicks).toHaveLength(1);
      expect(clicks[0]).toMatch(/^image\//);
      // …and it landed in Post mode with the composer open.
      expect(screen.getByTestId('composer-body')).toBeInTheDocument();
    });

    it('"Post a Video" opens the video picker in the same gesture', () => {
      renderComposer();
      const clicks: string[] = [];
      const spy = vi.spyOn(HTMLInputElement.prototype, 'click')
        .mockImplementation(function (this: HTMLInputElement) { clicks.push(this.accept); });
      try {
        fireEvent.click(screen.getByTestId('composer-tool-video'));
      } finally { spy.mockRestore(); }
      expect(clicks).toHaveLength(1);
      expect(clicks[0]).toMatch(/^video\//);
    });

    // Poll and Audio have NO backend (no poll model in commerce; weespas's upload allowlist is
    // images+video only). They must say so rather than silently do nothing — a button that no-ops is
    // indistinguishable from a broken one.
    it.each(['poll', 'audio'])('"%s" is announced as unavailable and never publishes', (key) => {
      renderComposer();
      fireEvent.click(screen.getByTestId(`composer-tool-${key}`));
      expect(toast.info).toHaveBeenCalledWith(expect.stringMatching(/coming soon/i));
      // It does NOT expand the composer or start any write.
      expect(screen.queryByTestId('composer-body')).toBeNull();
      expect(mockCreatePost).not.toHaveBeenCalled();
      expect(mockCreateListing).not.toHaveBeenCalled();
      // A REAL enabled button: it must stay focusable and clickable so keyboard/AT users can reach
      // the explanation. Neither `disabled` nor `aria-disabled` belongs on it — either would claim
      // "this does nothing" and swallow the message. The unavailability lives in the accessible NAME.
      const btn = screen.getByTestId(`composer-tool-${key}`) as HTMLButtonElement;
      expect(btn.disabled).toBe(false);
      expect(btn.getAttribute('aria-disabled')).toBeNull();
      expect(btn.getAttribute('aria-label')).toMatch(/coming soon/i);
    });
  });
});
