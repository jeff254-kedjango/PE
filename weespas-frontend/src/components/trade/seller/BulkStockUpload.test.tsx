import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../../api/commerce')>('../../../api/commerce');
  return { ...actual, postBulkStockCsv: vi.fn() };
});
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }),
}));

import { postBulkStockCsv, type CommerceSession } from '../../../api/commerce';
import BulkStockUpload from './BulkStockUpload';

const mockBulk = vi.mocked(postBulkStockCsv);
const SESSION: CommerceSession = { token: 'ctok', commerce_url: 'http://c' };

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <BulkStockUpload session={SESSION} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('BulkStockUpload', () => {
  it('renders the title, textarea, and upload button', () => {
    renderIt();
    expect(screen.getByRole('heading', { name: /Bulk update stock/i })).toBeInTheDocument();
    expect(screen.getByTestId('bulk-stock-textarea')).toBeInTheDocument();
    expect(screen.getByTestId('bulk-stock-submit')).toBeInTheDocument();
  });

  it('sends the pasted CSV and reports the summary on success', async () => {
    mockBulk.mockResolvedValue({ updated_count: 3, skipped_count: 0, updated_ids: ['a', 'b', 'c'] });
    renderIt();
    fireEvent.change(screen.getByTestId('bulk-stock-textarea'), {
      target: { value: 'a,10\nb,20\nc,30\n' },
    });
    fireEvent.click(screen.getByTestId('bulk-stock-submit'));
    await waitFor(() => {
      expect(mockBulk).toHaveBeenCalledWith(SESSION, 'a,10\nb,20\nc,30\n');
    });
    expect(await screen.findByTestId('bulk-stock-feedback')).toHaveTextContent(/Updated 3/i);
  });

  it('surfaces skipped count when unowned rows were dropped', async () => {
    mockBulk.mockResolvedValue({ updated_count: 2, skipped_count: 1, updated_ids: ['a', 'b'] });
    renderIt();
    fireEvent.change(screen.getByTestId('bulk-stock-textarea'), {
      target: { value: 'a,1\nb,2\nghost,3\n' },
    });
    fireEvent.click(screen.getByTestId('bulk-stock-submit'));
    expect(await screen.findByTestId('bulk-stock-feedback')).toHaveTextContent(/Updated 2, skipped 1/i);
  });

  it('surfaces the server error message on 422', async () => {
    mockBulk.mockRejectedValue(new Error('line 3: duplicate listing_id'));
    renderIt();
    fireEvent.change(screen.getByTestId('bulk-stock-textarea'), {
      target: { value: 'a,1\na,2\n' },
    });
    fireEvent.click(screen.getByTestId('bulk-stock-submit'));
    expect(await screen.findByTestId('bulk-stock-feedback')).toHaveTextContent(/duplicate listing_id/i);
  });

  it('does not fire when the textarea is empty', () => {
    renderIt();
    fireEvent.click(screen.getByTestId('bulk-stock-submit'));
    expect(mockBulk).not.toHaveBeenCalled();
  });

  it('clears the textarea on success (so a second click does not re-submit stale data)', async () => {
    mockBulk.mockResolvedValue({ updated_count: 1, skipped_count: 0, updated_ids: ['a'] });
    renderIt();
    const ta = screen.getByTestId('bulk-stock-textarea') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'a,1\n' } });
    fireEvent.click(screen.getByTestId('bulk-stock-submit'));
    await screen.findByTestId('bulk-stock-feedback');
    expect(ta.value).toBe('');
  });
});
