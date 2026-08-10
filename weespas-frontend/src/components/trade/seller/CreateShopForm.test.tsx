import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../../api/commerce')>('../../../api/commerce');
  return {
    ...actual,
    createShop: vi.fn(),
    uploadTradeMedia: vi.fn(),
    // The handle UX pings these two — always stub them so an unrelated test never accidentally
    // hits the network. The default probe answers "available" so it can't interfere with
    // pre-existing name/coord tests.
    checkHandleAvailable: vi.fn(),
    claimShopHandle: vi.fn(),
  };
});

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ toast }) }));

// Drive the geolocation hook deterministically (no real navigator.geolocation in jsdom).
const geo = { latitude: null as number | null, longitude: null as number | null, loading: false, error: null as string | null, requestLocation: vi.fn() };
vi.mock('../../../hooks/useGeolocation', () => ({ useGeolocation: () => geo }));

import {
  checkHandleAvailable, claimShopHandle, createShop, uploadTradeMedia,
} from '../../../api/commerce';
import CreateShopForm from './CreateShopForm';
import type { CommerceSession, ShopOut, TradeMediaUpload } from '../../../api/commerce';

const mockCreate = vi.mocked(createShop);
const mockUpload = vi.mocked(uploadTradeMedia);
const mockCheckHandle = vi.mocked(checkHandleAvailable);
const mockClaimHandle = vi.mocked(claimShopHandle);
const SESSION: CommerceSession = { token: 'ctok', commerce_url: 'http://c' };

function shopOut(): ShopOut {
  return { id: 'shop1', seller_id: 'sel1', name: 'Mama Njeri', avatar_url: null, banner_url: null, handle: null, property_uuid: null, lat: -1.29, lng: 36.82, created_at: '2026-06-29T00:00:00Z' };
}

function renderForm(onCreated = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <CreateShopForm session={SESSION} weespasToken="wtok" onClose={() => {}} onCreated={onCreated} />
    </QueryClientProvider>,
  );
  return onCreated;
}

const submit = () => fireEvent.submit(document.getElementById('create-shop-form') as HTMLFormElement);

beforeEach(() => {
  vi.clearAllMocks();
  geo.latitude = null; geo.longitude = null; geo.loading = false;
  mockCreate.mockResolvedValue(shopOut());
  // Default probe answers "available" — an empty box short-circuits before this fires, so this
  // only reaches the test in the handle-UX tests below (which override it as needed).
  mockCheckHandle.mockResolvedValue({ handle: '', available: true, reason: null });
  mockClaimHandle.mockResolvedValue({ ...shopOut(), handle: 'mama-mboga' });
});

describe('CreateShopForm', () => {
  it('does not submit until name, display name, and a valid lat/lng are present', async () => {
    renderForm();
    const create = screen.getByRole('button', { name: 'Create shop' });
    expect(create).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Shop name'), { target: { value: 'Mama Njeri' } });
    fireEvent.change(screen.getByLabelText('Your display name'), { target: { value: 'Njeri' } });
    expect(create).toBeDisabled();                // still no coordinates
    fireEvent.change(screen.getByLabelText('Latitude'), { target: { value: '-1.29' } });
    fireEvent.change(screen.getByLabelText('Longitude'), { target: { value: '36.82' } });
    await waitFor(() => expect(create).not.toBeDisabled());
  });

  it('rejects out-of-range coordinates', () => {
    renderForm();
    fireEvent.change(screen.getByLabelText('Shop name'), { target: { value: 'S' } });
    fireEvent.change(screen.getByLabelText('Your display name'), { target: { value: 'N' } });
    fireEvent.change(screen.getByLabelText('Latitude'), { target: { value: '120' } }); // > 90
    fireEvent.change(screen.getByLabelText('Longitude'), { target: { value: '36.82' } });
    expect(screen.getByRole('button', { name: 'Create shop' })).toBeDisabled();
  });

  it('"Use my location" requests geolocation; a resolved fix fills the coordinate fields', async () => {
    const qc = new QueryClient();
    const tree = () => (
      <QueryClientProvider client={qc}>
        <CreateShopForm session={SESSION} onClose={() => {}} onCreated={vi.fn()} />
      </QueryClientProvider>
    );
    const { rerender } = render(tree());

    fireEvent.click(screen.getByTestId('use-my-location'));
    expect(geo.requestLocation).toHaveBeenCalledTimes(1);

    // Simulate the hook resolving a position; the form's effect copies it into the fields.
    // (Fresh element each render so React doesn't bail out on identical-reference children.)
    geo.latitude = -1.234567; geo.longitude = 36.812345;
    rerender(tree());
    await waitFor(() => expect((screen.getByLabelText('Latitude') as HTMLInputElement).value).toBe('-1.234567'));
    expect((screen.getByLabelText('Longitude') as HTMLInputElement).value).toBe('36.812345');
  });

  it('creates the shop with trimmed values and reports the new id', async () => {
    const onCreated = renderForm();
    fireEvent.change(screen.getByLabelText('Shop name'), { target: { value: '  Mama Njeri  ' } });
    fireEvent.change(screen.getByLabelText('Your display name'), { target: { value: 'Njeri' } });
    fireEvent.change(screen.getByLabelText('Latitude'), { target: { value: '-1.29' } });
    fireEvent.change(screen.getByLabelText('Longitude'), { target: { value: '36.82' } });
    submit();
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    const body = mockCreate.mock.calls[0][1];
    expect(body.name).toBe('Mama Njeri');
    expect(body.display_name).toBe('Njeri');
    expect(body.lat).toBe(-1.29);
    expect(body.lng).toBe(36.82);
    // No images picked → no upload, no avatar/banner URLs sent.
    expect(mockUpload).not.toHaveBeenCalled();
    expect(body.avatar_url).toBeUndefined();
    expect(body.banner_url).toBeUndefined();
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('shop1'));
  });

  it('tells the seller their profile picture is the business logo', () => {
    renderForm();
    // The logo hint must state it serves as the business logo (the user's explicit ask).
    expect(screen.getByText(/business logo/i)).toBeTruthy();
  });

  // ─────────── handle UX (§8 shareable /shop/<handle>) ───────────

  it('handle field is optional — a blank handle still creates the shop and does not call claim', async () => {
    // Sanity: pre-existing tests already prove this by omission, but a NAMED test locks the
    // invariant so a future change that makes the field required fails a specific expectation.
    const onCreated = renderForm();
    fireEvent.change(screen.getByLabelText('Shop name'), { target: { value: 'Mama Njeri' } });
    fireEvent.change(screen.getByLabelText('Your display name'), { target: { value: 'Njeri' } });
    fireEvent.change(screen.getByLabelText('Latitude'), { target: { value: '-1.29' } });
    fireEvent.change(screen.getByLabelText('Longitude'), { target: { value: '36.82' } });
    submit();
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('shop1'));
    expect(mockClaimHandle).not.toHaveBeenCalled();
    expect(mockCheckHandle).not.toHaveBeenCalled();  // empty box short-circuits the probe
  });

  it('shows an inline "syntax" error immediately for a bad handle (pre-syntax, no probe)', async () => {
    renderForm();
    fireEvent.change(screen.getByTestId('shop-handle'), { target: { value: 'Bad--Name' } });
    // The pre-syntax check fires on the debounced value, so wait for the error to appear.
    await waitFor(() => expect(screen.getByTestId('shop-handle-error')).toBeInTheDocument());
    expect(screen.getByTestId('shop-handle-error').textContent).toMatch(/Letters, numbers and single hyphens/);
    // The probe MUST NOT fire — pre-syntax short-circuits it (no wasted round-trip on typos).
    expect(mockCheckHandle).not.toHaveBeenCalled();
    // Submit stays refused so long as the input is red, even with all other fields valid.
    fireEvent.change(screen.getByLabelText('Shop name'), { target: { value: 'Mama' } });
    fireEvent.change(screen.getByLabelText('Your display name'), { target: { value: 'Njeri' } });
    fireEvent.change(screen.getByLabelText('Latitude'), { target: { value: '-1.29' } });
    fireEvent.change(screen.getByLabelText('Longitude'), { target: { value: '36.82' } });
    expect(screen.getByRole('button', { name: 'Create shop' })).toBeDisabled();
  });

  it('shows the "reserved" error inline (server-side deny-list, but caught pre-emptively)', async () => {
    renderForm();
    fireEvent.change(screen.getByTestId('shop-handle'), { target: { value: 'admin' } });
    await waitFor(() => expect(screen.getByTestId('shop-handle-error').textContent).toMatch(/reserved/i));
    // Reserved is a pre-syntax rejection (client-side deny-list mirrors the server) — no probe.
    expect(mockCheckHandle).not.toHaveBeenCalled();
  });

  it('probes the server for a syntactically-valid handle and shows "Available" on true', async () => {
    mockCheckHandle.mockResolvedValue({ handle: 'nyama-choma', available: true, reason: null });
    renderForm();
    fireEvent.change(screen.getByTestId('shop-handle'), { target: { value: 'nyama-choma' } });
    await waitFor(() =>
      expect(mockCheckHandle).toHaveBeenCalledWith(SESSION, 'nyama-choma'),
    );
    await waitFor(() =>
      expect(screen.getByTestId('shop-handle-badge').textContent).toMatch(/Available/),
    );
  });

  it('shows "handle-taken" inline when the probe reports the name is unavailable', async () => {
    mockCheckHandle.mockResolvedValue({ handle: 'nyama-choma', available: false, reason: 'handle-taken' });
    renderForm();
    fireEvent.change(screen.getByTestId('shop-handle'), { target: { value: 'nyama-choma' } });
    await waitFor(() =>
      expect(screen.getByTestId('shop-handle-error').textContent).toMatch(/taken/i),
    );
    // A taken handle blocks submit even with the rest of the form filled.
    fireEvent.change(screen.getByLabelText('Shop name'), { target: { value: 'S' } });
    fireEvent.change(screen.getByLabelText('Your display name'), { target: { value: 'N' } });
    fireEvent.change(screen.getByLabelText('Latitude'), { target: { value: '-1.29' } });
    fireEvent.change(screen.getByLabelText('Longitude'), { target: { value: '36.82' } });
    expect(screen.getByRole('button', { name: 'Create shop' })).toBeDisabled();
  });

  it('claims the handle after createShop when one is set + available', async () => {
    // The probe's `handle` in the response MUST echo the lowercased+trimmed form the component
    // asked about (the race-guard requires equality). MamaMboga → mamamboga.
    mockCheckHandle.mockResolvedValue({ handle: 'mamamboga', available: true, reason: null });
    const onCreated = renderForm();
    fireEvent.change(screen.getByLabelText('Shop name'), { target: { value: 'Mama Njeri' } });
    fireEvent.change(screen.getByLabelText('Your display name'), { target: { value: 'Njeri' } });
    fireEvent.change(screen.getByLabelText('Latitude'), { target: { value: '-1.29' } });
    fireEvent.change(screen.getByLabelText('Longitude'), { target: { value: '36.82' } });
    fireEvent.change(screen.getByTestId('shop-handle'), { target: { value: 'MamaMboga' } });
    // Wait for the probe to settle before submitting (Available badge shows).
    await waitFor(() =>
      expect(screen.getByTestId('shop-handle-badge').textContent).toMatch(/Available/),
    );
    submit();
    // Order: create first, THEN claim on the created shop id (with the lowercased handle).
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mockClaimHandle).toHaveBeenCalledWith(SESSION, 'shop1', 'mamamboga'));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('shop1'));
    // Success toast, not the "shop created, handle failed" warning.
    expect(toast.success).toHaveBeenCalledWith('Shop created.');
  });

  it('surfaces a race-loss on the claim as a specific warning — the shop still gets created', async () => {
    // A race: probe said available, but between probe and claim someone else grabbed it → 409.
    mockCheckHandle.mockResolvedValue({ handle: 'racey', available: true, reason: null });
    mockClaimHandle.mockRejectedValue(new Error('handle-taken'));
    const onCreated = renderForm();
    fireEvent.change(screen.getByLabelText('Shop name'), { target: { value: 'Mama Njeri' } });
    fireEvent.change(screen.getByLabelText('Your display name'), { target: { value: 'Njeri' } });
    fireEvent.change(screen.getByLabelText('Latitude'), { target: { value: '-1.29' } });
    fireEvent.change(screen.getByLabelText('Longitude'), { target: { value: '36.82' } });
    fireEvent.change(screen.getByTestId('shop-handle'), { target: { value: 'racey' } });
    await waitFor(() =>
      expect(screen.getByTestId('shop-handle-badge').textContent).toMatch(/Available/),
    );
    submit();
    await waitFor(() => expect(mockClaimHandle).toHaveBeenCalledTimes(1));
    // Warning toast names the specific reason so the seller can retry from the dashboard.
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/handle claim failed:.*taken/i)),
    );
    // The shop still exists — onCreated fires, no rollback.
    expect(onCreated).toHaveBeenCalledWith('shop1');
    // No spurious success toast (rule 5: error state must be legible, not double-signalled).
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('uploads a picked logo + banner and sends their URLs as avatar_url/banner_url', async () => {
    // Each uploadTradeMedia call returns a distinct URL so we can assert the mapping (logo→avatar,
    // banner→banner). The component uploads them in order: logo first, then banner.
    mockUpload
      .mockResolvedValueOnce({ uploaded: 1, images: [{ url: '/uploads/trade/images/logo.png', thumbnail_url: '', mime_type: 'image/png', file_size: 1 }], video: null } as TradeMediaUpload)
      .mockResolvedValueOnce({ uploaded: 1, images: [{ url: '/uploads/trade/images/cover.jpg', thumbnail_url: '', mime_type: 'image/jpeg', file_size: 1 }], video: null } as TradeMediaUpload);

    const onCreated = renderForm();
    fireEvent.change(screen.getByLabelText('Shop name'), { target: { value: 'Mama Njeri' } });
    fireEvent.change(screen.getByLabelText('Your display name'), { target: { value: 'Njeri' } });
    fireEvent.change(screen.getByLabelText('Latitude'), { target: { value: '-1.29' } });
    fireEvent.change(screen.getByLabelText('Longitude'), { target: { value: '36.82' } });

    const logoFile = new File(['x'], 'logo.png', { type: 'image/png' });
    const bannerFile = new File(['y'], 'cover.jpg', { type: 'image/jpeg' });
    fireEvent.change(screen.getByTestId('shop-logo'), { target: { files: [logoFile] } });
    fireEvent.change(screen.getByTestId('shop-banner'), { target: { files: [bannerFile] } });

    submit();
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockUpload).toHaveBeenCalledTimes(2);
    const body = mockCreate.mock.calls[0][1];
    expect(body.avatar_url).toBe('/uploads/trade/images/logo.png');
    expect(body.banner_url).toBe('/uploads/trade/images/cover.jpg');
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('shop1'));
  });
});
