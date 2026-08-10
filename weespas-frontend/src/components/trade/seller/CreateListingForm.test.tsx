import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock the commerce write surface; keep the pure helpers (majorToCents) real so we test the
// real major→cents conversion the form relies on.
vi.mock('../../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../../api/commerce')>('../../../api/commerce');
  return { ...actual, uploadTradeMedia: vi.fn(), createListing: vi.fn(), createShop: vi.fn(), adjustStock: vi.fn() };
});

// useToast throws without a provider; mock it so we can assert error toasts directly.
const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ toast }) }));

import { uploadTradeMedia, createListing } from '../../../api/commerce';
import CreateListingForm from './CreateListingForm';
import type { CommerceSession, ListingOut, StorefrontShop, TradeMediaUpload } from '../../../api/commerce';

const mockUpload = vi.mocked(uploadTradeMedia);
const mockCreate = vi.mocked(createListing);

const SESSION: CommerceSession = { token: 'ctok', commerce_url: 'http://c' };

const SHOP: StorefrontShop = {
  shop: { id: 'shop1', seller_id: 'sel1', name: 'Mama Njeri', avatar_url: null, banner_url: null, handle: null, property_uuid: null, lat: -1.29, lng: 36.82, created_at: '2026-06-29T00:00:00Z' },
  listings: [],
};

function makeListingOut(): ListingOut {
  return {
    id: 'l1', shop_id: 'shop1', seller_id: 'sel1', property_uuid: null, title: 'X', description: null,
    price_cents: 15000, currency: 'KES', media_urls: [], intent_weight: 1, is_active: true,
    stock_qty: 1, low_stock_threshold: 0, pricing_mode: 'fixed', is_short_video: false, post_kind: 'product',
    is_out_of_stock: false, is_low_stock: false, promo_mode: null, promo_started_at: null,
    promo_expires_at: null, is_promoted: false,
    flash_price_cents: null, flash_started_at: null, flash_expires_at: null, flash_reference_cents: null, is_flash_active: false,
    created_at: '2026-06-29T00:00:00Z',
  };
}

function renderForm(props: Partial<React.ComponentProps<typeof CreateListingForm>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CreateListingForm
        session={SESSION}
        weespasToken="wtok"
        shops={[SHOP]}
        defaultShopId="shop1"
        onClose={props.onClose ?? (() => {})}
        {...props}
      />
    </QueryClientProvider>,
  );
}

function fillTitleAndPrice(title = 'Fresh sukuma', price = '150') {
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: title } });
  fireEvent.change(screen.getByLabelText('Price (KES)'), { target: { value: price } });
}

const submitForm = () =>
  fireEvent.submit(document.getElementById('create-listing-form') as HTMLFormElement);

beforeEach(() => {
  vi.clearAllMocks();
  mockCreate.mockResolvedValue(makeListingOut());
});

describe('CreateListingForm', () => {
  it('converts the price from major units to integer cents (150 → 15000)', async () => {
    renderForm();
    fillTitleAndPrice('Fresh sukuma', '150');
    submitForm();
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    const body = mockCreate.mock.calls[0][2];
    expect(body.price_cents).toBe(15000);
    expect(body.is_short_video).toBe(false);
    expect(body.media_urls).toEqual([]);
    // No media selected → upload endpoint never hit.
    expect(mockUpload).not.toHaveBeenCalled();
  });

  it('threads a trimmed product description through to createListing', async () => {
    renderForm();
    fillTitleAndPrice('Fresh sukuma', '150');
    fireEvent.change(screen.getByTestId('listing-description'), {
      target: { value: '  Crisp greens.\n\nPicked today.  ' },
    });
    submitForm();
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate.mock.calls[0][2].description).toBe('Crisp greens.\n\nPicked today.');
  });

  it('sends a null description when the field is left blank', async () => {
    renderForm();
    fillTitleAndPrice('Fresh sukuma', '150');
    submitForm();
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate.mock.calls[0][2].description).toBeNull();
  });

  it('blocks a short-video post with no video (does not publish an empty kind)', async () => {
    renderForm();
    fillTitleAndPrice();
    fireEvent.click(screen.getByTestId('short-video-toggle'));
    submitForm();
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(mockCreate).not.toHaveBeenCalled();
    expect(mockUpload).not.toHaveBeenCalled();
  });

  it('uploads media BEFORE creating the listing and threads the returned URL through', async () => {
    const order: string[] = [];
    const uploaded: TradeMediaUpload = {
      uploaded: 1, images: [], video: { url: '/uploads/trade/videos/v.mp4', thumbnail_url: '/uploads/trade/videos/v.mp4', mime_type: 'video/mp4', file_size: 10 },
    };
    mockUpload.mockImplementation(async () => { order.push('upload'); return uploaded; });
    mockCreate.mockImplementation(async () => { order.push('create'); return makeListingOut(); });

    renderForm();
    fillTitleAndPrice();
    fireEvent.click(screen.getByTestId('short-video-toggle'));
    // Attach a small fake video via the uploader's hidden video input.
    const file = new File(['x'], 'clip.mp4', { type: 'video/mp4' });
    const videoBtn = screen.getByTestId('add-video');
    fireEvent.click(videoBtn);
    const videoInput = document.querySelector('input[type="file"][accept*="video"]') as HTMLInputElement;
    fireEvent.change(videoInput, { target: { files: [file] } });

    submitForm();
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(order).toEqual(['upload', 'create']);
    expect(mockUpload).toHaveBeenCalledWith('wtok', expect.objectContaining({ video: file }));
    const body = mockCreate.mock.calls[0][2];
    expect(body.media_urls).toEqual(['/uploads/trade/videos/v.mp4']);
    expect(body.is_short_video).toBe(true);
  });

  it('auto-marks a listing as a short video when a video is attached (no toggle click needed)', async () => {
    const uploaded: TradeMediaUpload = {
      uploaded: 1, images: [],
      video: { url: '/uploads/trade/videos/v.mp4', thumbnail_url: '/uploads/trade/videos/v.mp4', mime_type: 'video/mp4', file_size: 10 },
    };
    mockUpload.mockResolvedValue(uploaded);
    renderForm();
    fillTitleAndPrice();
    // Attach a video WITHOUT ever clicking the short-video toggle.
    const file = new File(['x'], 'clip.mp4', { type: 'video/mp4' });
    fireEvent.click(screen.getByTestId('add-video'));
    const videoInput = document.querySelector('input[type="file"][accept*="video"]') as HTMLInputElement;
    fireEvent.change(videoInput, { target: { files: [file] } });
    // The toggle should have auto-checked.
    expect((screen.getByTestId('short-video-toggle') as HTMLInputElement).checked).toBe(true);

    submitForm();
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate.mock.calls[0][2].is_short_video).toBe(true);
  });

  it('disables the publish button while the create mutation is in flight', async () => {
    let resolve!: (v: ListingOut) => void;
    mockCreate.mockImplementation(() => new Promise<ListingOut>((r) => { resolve = r; }));
    renderForm();
    fillTitleAndPrice();
    const publish = screen.getByRole('button', { name: 'Publish' });
    submitForm();
    await waitFor(() => expect(publish).toBeDisabled());
    resolve(makeListingOut());
  });
});
