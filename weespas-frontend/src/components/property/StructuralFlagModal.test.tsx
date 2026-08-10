import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Auth user role is swapped per-test via this mutable holder.
let mockUser: { roles: string[] } = { roles: ['professional'] };
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ token: 'tok', user: mockUser }),
}));

vi.mock('../../api/structuralFlags', async () => {
  const actual = await vi.importActual<typeof import('../../api/structuralFlags')>(
    '../../api/structuralFlags',
  );
  return { ...actual, createStructuralFlag: vi.fn() };
});

import { createStructuralFlag } from '../../api/structuralFlags';
import { ToastProvider } from '../../context/ToastContext';
import StructuralFlagModal from './StructuralFlagModal';

const mockCreate = vi.mocked(createStructuralFlag);

function renderModal() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <StructuralFlagModal
          isOpen
          listingId="L1"
          aoiCode="south_c"
          insarBuildingId={42}
          onClose={() => {}}
        />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockUser = { roles: ['professional'] };
  mockCreate.mockResolvedValue({
    id: 'f1', aoi_code: 'south_c', insar_building_id: 42, state: 2,
    source: 'engineer', observed_at: null, note: null, granted_by: 'u1',
  });
});
afterEach(() => { vi.clearAllMocks(); });

describe('StructuralFlagModal', () => {
  it('hides the authority-only "Condemned" option for a professional', () => {
    renderModal();
    expect(screen.getByText('Cleared')).toBeTruthy();
    expect(screen.getByText('Unsafe')).toBeTruthy();
    expect(screen.queryByText('Condemned')).toBeNull();
  });

  it('offers "Condemned" to an authority', () => {
    mockUser = { roles: ['authority'] };
    renderModal();
    expect(screen.getByText('Condemned')).toBeTruthy();
  });

  it('submits the selected judgement with role-derived source', async () => {
    renderModal();
    fireEvent.click(screen.getByText('Record judgement'));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    const [, body] = mockCreate.mock.calls[0];
    expect(body).toMatchObject({ aoi_code: 'south_c', insar_building_id: 42, state: 2, source: 'engineer' });
  });

  it('surfaces a backend error (e.g. 403) without closing', async () => {
    mockCreate.mockRejectedValue(new Error('only an authority may set AUTH_UNSAFE'));
    renderModal();
    fireEvent.click(screen.getByText('Record judgement'));
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
  });

  // Regression: the rounded panel must NOT be the scroll container, else a
  // scrollbar squares off the right-hand corners (top-right + bottom-right). The
  // fix moves scrolling to an inner .sf-modal__scroll wrapper that the panel clips.
  it('puts the scrollbar on an inner wrapper, not the rounded panel', () => {
    renderModal();
    // The modal renders through a portal to document.body, so query the document.
    const panel = document.querySelector('.sf-modal');
    const scroll = document.querySelector('.sf-modal__scroll');
    expect(panel).toBeTruthy();
    expect(scroll).toBeTruthy();
    // The scroll wrapper is a direct child of the rounded panel (so the panel
    // clips it) and the form content lives inside the scroll wrapper.
    expect(scroll!.parentElement).toBe(panel);
    expect(scroll!.querySelector('.sf-modal__states')).toBeTruthy();
  });

  // Buttons use the canonical app button system (utilities.css), not ad-hoc classes.
  it('styles the actions with the canonical .btn classes', () => {
    renderModal();
    const cancel = screen.getByText('Cancel');
    const record = screen.getByText('Record judgement');
    expect(cancel.className).toContain('btn');
    expect(cancel.className).toContain('btn-secondary');
    expect(record.className).toContain('btn');
    expect(record.className).toContain('btn-primary');
  });

  // The action row owns its own layout class (not the unimported role-app one).
  it('wraps the buttons in the self-contained .sf-modal__actions row', () => {
    renderModal();
    const row = document.querySelector('.sf-modal__actions');
    expect(row).toBeTruthy();
    expect(row!.querySelectorAll('button.btn').length).toBe(2);
    expect(document.querySelector('.role-app-modal__actions')).toBeNull();
  });

  // After a successful flag, the certifier sees the full acknowledgment message.
  it('shows the review + identity-linkage acknowledgment on success', async () => {
    renderModal();
    fireEvent.click(screen.getByText('Record judgement'));
    await waitFor(() =>
      expect(
        screen.getByText(/recorded your assessment and are currently reviewing it/i),
      ).toBeTruthy(),
    );
    expect(screen.getByText(/your identity will be linked to this record/i)).toBeTruthy();
    expect(
      screen.getByText(/contributing to the safety and well-being of others/i),
    ).toBeTruthy();
  });
});
