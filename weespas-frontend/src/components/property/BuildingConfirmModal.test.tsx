import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';

// The modal owns load/confirm/success lifecycle; the candidate hooks and the (Leaflet-backed)
// map child are mocked so the test stays DOM-only and focused on that lifecycle — including
// the regression that a successful confirm must keep showing the "Thanks!" screen and not be
// torn down early (PropertyDetails gates the mount on confirmOpen alone for exactly this).
const mockMutateAsync = vi.fn();
let mockPending = false;
const mockCandidates = vi.fn(() => ({
  data: { candidates: [{ insar_building_id: 7 }] }, isLoading: false, isError: false,
}));
vi.mock('../../hooks/useListingCandidates', () => ({
  useListingCandidates: () => mockCandidates(),
  useConfirmListingBuilding: () => ({ mutateAsync: mockMutateAsync, isPending: mockPending }),
}));

// Render the map child as a single "pick + confirm" button so we can drive onConfirm without
// Leaflet. (BuildingConfirmMap has its own dedicated test for the picker behaviour.)
vi.mock('../map/BuildingConfirmMap', () => ({
  default: ({ onConfirm, confirming }: { onConfirm: (id: number) => void; confirming?: boolean }) => (
    <button type="button" disabled={confirming} onClick={() => onConfirm(7)}>
      pick-7
    </button>
  ),
}));

const mockToast = { success: vi.fn(), error: vi.fn() };
vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ toast: mockToast }),
}));

import BuildingConfirmModal from './BuildingConfirmModal';

beforeEach(() => {
  mockPending = false;
  mockMutateAsync.mockResolvedValue({ coverage: 'monitored', danger_level: 2 });
  mockCandidates.mockReturnValue({
    data: { candidates: [{ insar_building_id: 7 }] }, isLoading: false, isError: false,
  } as never);
});
afterEach(() => { vi.clearAllMocks(); vi.useRealTimers(); });

describe('BuildingConfirmModal', () => {
  it('renders the picker once candidates have loaded', () => {
    render(<BuildingConfirmModal listingId="L1" onClose={() => {}} />);
    expect(screen.getByText('pick-7')).toBeTruthy();
    expect(screen.getByText(/Confirm your building/)).toBeTruthy();
  });

  it('shows a loading state while candidates are in flight', () => {
    mockCandidates.mockReturnValue({ data: undefined, isLoading: true, isError: false } as never);
    render(<BuildingConfirmModal listingId="L1" onClose={() => {}} />);
    expect(screen.getByText(/Loading nearby buildings/)).toBeTruthy();
  });

  it('surfaces an error state without ever implying a (mis)match', () => {
    mockCandidates.mockReturnValue({ data: undefined, isLoading: false, isError: true } as never);
    render(<BuildingConfirmModal listingId="L1" onClose={() => {}} />);
    expect(screen.getByText(/Couldn't load nearby buildings/)).toBeTruthy();
    expect(screen.queryByText('pick-7')).toBeNull();
  });

  it('persists the choice and shows the success screen on confirm', async () => {
    render(<BuildingConfirmModal listingId="L1" onClose={() => {}} />);
    fireEvent.click(screen.getByText('pick-7'));
    await waitFor(() => expect(mockMutateAsync).toHaveBeenCalledWith(7));
    await waitFor(() => expect(screen.getByText(/matched to the right building/)).toBeTruthy());
    expect(mockToast.success).toHaveBeenCalled();
  });

  it('keeps the modal open showing success even after confirm settles (no early teardown)', async () => {
    vi.useFakeTimers();
    const onClose = vi.fn();
    render(<BuildingConfirmModal listingId="L1" onClose={onClose} />);
    fireEvent.click(screen.getByText('pick-7'));
    // Flush the mutateAsync microtask, then assert the success copy is shown and onClose has
    // NOT yet fired (it is scheduled ~1.6s out). The parent mount no longer depends on
    // canConfirm, so a coverage flip can't yank this screen away.
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText(/matched to the right building/)).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1600); });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('surfaces a confirm failure as a toast and stays on the picker', async () => {
    mockMutateAsync.mockRejectedValue(new Error('nope'));
    render(<BuildingConfirmModal listingId="L1" onClose={() => {}} />);
    fireEvent.click(screen.getByText('pick-7'));
    await waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    expect(screen.getByText('pick-7')).toBeTruthy();
  });
});
