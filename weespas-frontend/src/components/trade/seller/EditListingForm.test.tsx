import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock the commerce write surface; keep pure helpers (centsToMajor/majorToCents) real so the
// prefill + edit conversion is exercised for real.
vi.mock('../../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../../api/commerce')>('../../../api/commerce');
  return { ...actual, uploadTradeMedia: vi.fn(), updateListing: vi.fn(), deleteListing: vi.fn() };
});

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ toast }) }));

import { uploadTradeMedia, updateListing, deleteListing } from '../../../api/commerce';
import EditListingForm from './EditListingForm';
import type { CommerceSession, ListingOut, TradeMediaUpload } from '../../../api/commerce';

const mockUpload = vi.mocked(uploadTradeMedia);
const mockUpdate = vi.mocked(updateListing);
const mockDelete = vi.mocked(deleteListing);

const SESSION: CommerceSession = { token: 'ctok', commerce_url: 'http://c' };

function makeListing(over: Partial<ListingOut> = {}): ListingOut {
  return {
    id: 'l1', shop_id: 'shop1', seller_id: 'sel1', property_uuid: null,
    title: 'Old Title', description: 'old body', price_cents: 15000, currency: 'KES',
    media_urls: ['/uploads/trade/images/a.jpg'], intent_weight: 1, is_active: true,
    stock_qty: 4, low_stock_threshold: 2, pricing_mode: 'fixed', is_short_video: false, post_kind: 'product',
    is_out_of_stock: false, is_low_stock: false, promo_mode: null, promo_started_at: null,
    promo_expires_at: null, is_promoted: false,
    flash_price_cents: null, flash_started_at: null, flash_expires_at: null, flash_reference_cents: null, is_flash_active: false,
    created_at: '2026-06-29T00:00:00Z', ...over,
  };
}

function renderForm(listing = makeListing(), onClose = () => {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <EditListingForm session={SESSION} weespasToken="wtok" listing={listing} onClose={onClose} />
    </QueryClientProvider>,
  );
}

const submitForm = () => fireEvent.submit(document.getElementById('edit-listing-form') as HTMLFormElement);

beforeEach(() => {
  vi.clearAllMocks();
  mockUpdate.mockResolvedValue(makeListing());
  mockDelete.mockResolvedValue(undefined);
});

describe('EditListingForm', () => {
  it('prefills the fields from the listing (title + price in major units)', () => {
    renderForm();
    expect((screen.getByLabelText('Title') as HTMLInputElement).value).toBe('Old Title');
    expect((screen.getByLabelText('Price (KES)') as HTMLInputElement).value).toBe('150');
  });

  it('hides the stock-on-hand field (edited via the POS control, not here)', () => {
    renderForm();
    expect(screen.queryByLabelText('Stock on hand')).toBeNull();
    // Low-stock alert is still editable.
    expect(screen.getByLabelText('Low-stock alert at')).toBeTruthy();
  });

  it('PATCHes only edited fields and omits media when none was added', async () => {
    const onClose = vi.fn();
    renderForm(makeListing(), onClose);
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'New Title' } });
    submitForm();
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    const [, id, body] = mockUpdate.mock.calls[0];
    expect(id).toBe('l1');
    expect(body.title).toBe('New Title');
    expect(body.price_cents).toBe(15000);
    // No new media added → media_urls left out of the patch (existing media untouched).
    expect(body.media_urls).toBeUndefined();
    expect(mockUpload).not.toHaveBeenCalled();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('appends newly-uploaded media to the existing media_urls', async () => {
    const uploaded: TradeMediaUpload = {
      uploaded: 1,
      images: [{ url: '/uploads/trade/images/b.jpg', thumbnail_url: '/uploads/trade/images/b.jpg', mime_type: 'image/jpeg', file_size: 9 }],
      video: null,
    };
    mockUpload.mockResolvedValue(uploaded);
    renderForm();
    const file = new File(['x'], 'b.jpg', { type: 'image/jpeg' });
    const imgInput = document.querySelector('input[type="file"][accept*="image"]') as HTMLInputElement;
    fireEvent.change(imgInput, { target: { files: [file] } });
    submitForm();
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate.mock.calls[0][2].media_urls).toEqual([
      '/uploads/trade/images/a.jpg', '/uploads/trade/images/b.jpg',
    ]);
  });

  it('requires confirmation before deleting, then calls deleteListing', async () => {
    const onClose = vi.fn();
    renderForm(makeListing(), onClose);
    // First click opens the confirm step — delete is NOT yet called.
    fireEvent.click(screen.getByTestId('edit-delete'));
    expect(mockDelete).not.toHaveBeenCalled();
    expect(screen.getByTestId('delete-confirm')).toBeTruthy();
    // Confirm.
    fireEvent.click(screen.getByTestId('delete-confirm-yes'));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('can back out of the delete confirmation without deleting', () => {
    renderForm();
    fireEvent.click(screen.getByTestId('edit-delete'));
    fireEvent.click(screen.getByText('Keep it'));
    expect(mockDelete).not.toHaveBeenCalled();
    // Back to the edit form.
    expect(document.getElementById('edit-listing-form')).toBeTruthy();
  });
});
