import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../../api/commerce')>('../../../api/commerce');
  return { ...actual, getSponsoredCapStatus: vi.fn(), applySponsoredCap: vi.fn() };
});

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ toast }) }));

import { getSponsoredCapStatus, applySponsoredCap } from '../../../api/commerce';
import SponsoredCapChooser from './SponsoredCapChooser';
import type { CommerceSession, ShopOut, CapOverrideStatusOut, CapOverrideOut } from '../../../api/commerce';

const mockStatus = vi.mocked(getSponsoredCapStatus);
const mockApply = vi.mocked(applySponsoredCap);
const SESSION: CommerceSession = { token: 'ctok', commerce_url: 'http://c' };

function shop(): ShopOut {
  return {
    id: 's1', seller_id: 'sel1', name: 'Mama Mboga', avatar_url: null, banner_url: null,
    handle: null, property_uuid: null, lat: -1.29, lng: 36.82, created_at: '2026-06-29T00:00:00Z',
  };
}

// Server-authoritative status: the FE must read max_cap / default_cap from HERE, never hard-code.
function status(over: CapOverrideOut | null = null, maxCap = 10, defaultCap = 2): CapOverrideStatusOut {
  return { override: over, max_cap: maxCap, default_cap: defaultCap };
}

function override(partial: Partial<CapOverrideOut> = {}): CapOverrideOut {
  return {
    id: 'o1', shop_id: 's1', requested_cap: 5, status: 'pending', approved_cap: null,
    decided_by: null, decided_at: null, ...partial,
  };
}

function renderChooser() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SponsoredCapChooser session={SESSION} shop={shop()} onClose={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockStatus.mockResolvedValue(status());
  mockApply.mockResolvedValue(override());
});

describe('SponsoredCapChooser', () => {
  it('reads the non-destructive GET status on open (never POSTs to load)', async () => {
    renderChooser();
    await waitFor(() => expect(screen.getByTestId('spcap-status')).toBeInTheDocument());
    expect(mockStatus).toHaveBeenCalledWith(SESSION, 's1');
    expect(mockApply).not.toHaveBeenCalled(); // opening the modal must not mutate
  });

  it('shows the server default cap and "no request yet" when never applied', async () => {
    renderChooser();
    await waitFor(() => expect(screen.getByTestId('spcap-status')).toBeInTheDocument());
    expect(screen.getByText(/No request yet/)).toBeInTheDocument();
    expect(screen.getByText(/Standard cap: 2 slots/)).toBeInTheDocument();
  });

  it('bounds the input max to the SERVER max_cap, not a hard-coded value', async () => {
    mockStatus.mockResolvedValue(status(null, 7)); // server says ceiling is 7
    renderChooser();
    await waitFor(() => expect(screen.getByTestId('spcap-input')).toBeInTheDocument());
    expect(screen.getByTestId('spcap-input')).toHaveAttribute('max', '7');
    expect(screen.getByText(/max 7/)).toBeInTheDocument();
  });

  it('disables submit for an out-of-range value and enables it in range', async () => {
    renderChooser();
    await waitFor(() => expect(screen.getByTestId('spcap-input')).toBeInTheDocument());
    const submit = screen.getByTestId('spcap-submit');
    expect(submit).toBeDisabled(); // empty
    fireEvent.change(screen.getByTestId('spcap-input'), { target: { value: '99' } }); // > max 10
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByTestId('spcap-input'), { target: { value: '4' } });
    expect(submit).not.toBeDisabled();
  });

  it('submits the requested cap via applySponsoredCap and toasts success', async () => {
    renderChooser();
    await waitFor(() => expect(screen.getByTestId('spcap-input')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('spcap-input'), { target: { value: '4' } });
    fireEvent.click(screen.getByTestId('spcap-submit'));
    await waitFor(() => expect(mockApply).toHaveBeenCalledWith(SESSION, 's1', 4));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
  });

  it('warns that re-applying resets an already-approved cap to pending', async () => {
    mockStatus.mockResolvedValue(status(override({ status: 'approved', approved_cap: 6 })));
    renderChooser();
    await waitFor(() => expect(screen.getByTestId('spcap-status')).toBeInTheDocument());
    expect(screen.getByText(/up to/)).toBeInTheDocument();
    expect(screen.getByRole('note')).toHaveTextContent(/re-opens your request/i);
  });
});
