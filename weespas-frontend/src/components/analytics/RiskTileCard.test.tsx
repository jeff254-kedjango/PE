import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../api/analytics', () => ({ fetchRiskSummary: vi.fn() }));
import { fetchRiskSummary } from '../../api/analytics';
import RiskTileCard from './RiskTileCard';

const mockFetch = vi.mocked(fetchRiskSummary);

function renderTile() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RiskTileCard token="tok" />
    </QueryClientProvider>,
  );
}

const summary = (over: Partial<ReturnType<typeof base>> = {}) => ({ ...base(), ...over });
const base = () => ({
  coverage: { monitored: 9, not_monitored: 148, pending: 0, unavailable: 0 },
  monitored: 9, not_monitored: 148, pending: 0, unavailable: 0, unsafe_listings: 0,
});

beforeEach(() => { mockFetch.mockResolvedValue(summary() as never); });
afterEach(() => { vi.clearAllMocks(); });

describe('RiskTileCard', () => {
  it('renders the coverage mix counts', async () => {
    renderTile();
    await waitFor(() => expect(screen.getByText('9')).toBeTruthy());
    expect(screen.getByText('148')).toBeTruthy();
    expect(screen.getByText('Monitored')).toBeTruthy();
    expect(screen.getByText('Not monitored')).toBeTruthy();
  });

  it('goes hot when there are unsafe-flagged listings', async () => {
    mockFetch.mockResolvedValue(summary({ unsafe_listings: 2 }) as never);
    const { container } = renderTile();
    await waitFor(() => expect(container.querySelector('.risk-tile__alarm--hot')).toBeTruthy());
    expect(screen.getByText('2')).toBeTruthy();
  });

  it('stays calm (not hot) when zero unsafe listings', async () => {
    const { container } = renderTile();
    await waitFor(() => expect(container.querySelector('.risk-tile__alarm')).toBeTruthy());
    expect(container.querySelector('.risk-tile__alarm--hot')).toBeNull();
  });

  it('shows an error state if the fetch fails', async () => {
    mockFetch.mockRejectedValue(new Error('boom'));
    const { container } = renderTile();
    // The hook retries once (baseOpts.retry: 1), so allow extra time for the
    // error state to settle before asserting.
    await waitFor(
      () => expect(container.querySelector('.chart-card--error')).toBeTruthy(),
      { timeout: 3000 },
    );
  });
});
