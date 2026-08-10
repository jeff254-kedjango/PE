import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../api/commerce')>('../../api/commerce');
  return { ...actual, listPendingSponsoredCaps: vi.fn(), decideSponsoredCap: vi.fn() };
});

const SESSION = { token: 'ctok', commerce_url: 'http://c' };
vi.mock('../../hooks/useCommerceSession', () => ({
  useCommerceSession: () => ({ session: SESSION, isLoading: false, error: null }),
}));

import { listPendingSponsoredCaps, decideSponsoredCap } from '../../api/commerce';
import SponsoredCapQueueCard from './SponsoredCapQueueCard';
import type { CapOverrideOut, PendingCapListOut } from '../../api/commerce';

const mockList = vi.mocked(listPendingSponsoredCaps);
const mockDecide = vi.mocked(decideSponsoredCap);

function override(partial: Partial<CapOverrideOut> = {}): CapOverrideOut {
  return {
    id: 'o1', shop_id: 'shop-abcdef123', requested_cap: 5, status: 'pending',
    approved_cap: null, decided_by: null, decided_at: null, ...partial,
  };
}

function pending(overrides: CapOverrideOut[] = [override()], maxCap = 10): PendingCapListOut {
  return { overrides, max_cap: maxCap };
}

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SponsoredCapQueueCard />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockList.mockResolvedValue(pending());
  mockDecide.mockResolvedValue(override({ status: 'approved', approved_cap: 5 }));
});

describe('SponsoredCapQueueCard', () => {
  it('lists pending applications with the requested cap', async () => {
    renderCard();
    await waitFor(() => expect(screen.getByTestId('spcapq-item')).toBeInTheDocument());
    expect(screen.getByText(/requested/)).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
  });

  it('shows the empty state when nothing is pending', async () => {
    mockList.mockResolvedValue(pending([]));
    renderCard();
    await waitFor(() => expect(screen.getByText(/Nothing awaiting review/)).toBeInTheDocument());
  });

  it('approve sends approve:true with the (server-bounded) cap, defaulting to the requested value', async () => {
    renderCard();
    await waitFor(() => expect(screen.getByTestId('spcapq-approve-o1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('spcapq-approve-o1'));
    await waitFor(() => expect(mockDecide).toHaveBeenCalledWith(
      SESSION, 'o1', { approve: true, approvedCap: 5 },
    ));
  });

  it('reject sends approve:false', async () => {
    renderCard();
    await waitFor(() => expect(screen.getByTestId('spcapq-reject-o1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('spcapq-reject-o1'));
    await waitFor(() => expect(mockDecide).toHaveBeenCalledWith(
      SESSION, 'o1', { approve: false, approvedCap: undefined },
    ));
  });

  it('bounds the approve input to the SERVER max_cap and blocks an over-ceiling approval', async () => {
    mockList.mockResolvedValue(pending([override()], 7));
    renderCard();
    await waitFor(() => expect(screen.getByTestId('spcapq-amount-o1')).toBeInTheDocument());
    expect(screen.getByTestId('spcapq-amount-o1')).toHaveAttribute('max', '7');
    fireEvent.change(screen.getByTestId('spcapq-amount-o1'), { target: { value: '99' } });
    expect(screen.getByTestId('spcapq-approve-o1')).toBeDisabled();
    expect(mockDecide).not.toHaveBeenCalled();
  });
});
