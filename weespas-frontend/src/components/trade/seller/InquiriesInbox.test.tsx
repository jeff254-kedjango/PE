import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../../api/commerce')>('../../../api/commerce');
  return { ...actual, getMyInquiries: vi.fn(), markInquiryRead: vi.fn() };
});

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ toast }) }));

import { getMyInquiries, markInquiryRead } from '../../../api/commerce';
import InquiriesInbox, { unreadCount } from './InquiriesInbox';
import type { CommerceSession, InquiryOut, InquiryPage } from '../../../api/commerce';

const mockList = vi.mocked(getMyInquiries);
const mockRead = vi.mocked(markInquiryRead);
const SESSION: CommerceSession = { token: 'ctok', commerce_url: 'http://c' };

function inq(id: string, isRead: boolean, fromUserName: string | null = 'Asha Kimani'): InquiryOut {
  return {
    id, listing_id: 'l1', listing_title: 'Sukuma 1 bunch', seller_id: 'sel1',
    from_user_uuid: 'buyer-uuid', from_user_name: fromUserName,
    message: 'Is this available?', is_read: isRead, created_at: '2026-06-29T10:00:00Z',
  };
}

function page(items: InquiryOut[]): InquiryPage { return { items, next_cursor: null }; }

function renderInbox() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <InquiriesInbox session={SESSION} />
    </QueryClientProvider>,
  );
}

beforeEach(() => { vi.clearAllMocks(); });

describe('unreadCount helper', () => {
  it('counts only unread items, tolerating undefined', () => {
    expect(unreadCount(undefined)).toBe(0);
    expect(unreadCount([inq('a', false), inq('b', true), inq('c', false)])).toBe(2);
  });
});

describe('InquiriesInbox', () => {
  it('styles unread rows and shows a Mark read action only on them', async () => {
    mockList.mockResolvedValue(page([inq('a', false), inq('b', true)]));
    renderInbox();
    await waitFor(() => expect(screen.getAllByTestId('inbox-row')).toHaveLength(2));
    const rows = screen.getAllByTestId('inbox-row');
    expect(rows[0].className).toContain('inbox__row--unread');
    expect(rows[1].className).not.toContain('inbox__row--unread');
    // Only the unread row offers "Mark read".
    expect(screen.getAllByTestId('inbox-mark-read')).toHaveLength(1);
  });

  it('Mark read calls markInquiryRead with the inquiry id', async () => {
    mockList.mockResolvedValue(page([inq('a', false)]));
    mockRead.mockResolvedValue(undefined);
    renderInbox();
    await waitFor(() => expect(screen.getByTestId('inbox-mark-read')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('inbox-mark-read'));
    await waitFor(() => expect(mockRead).toHaveBeenCalledTimes(1));
    expect(mockRead.mock.calls[0][1]).toBe('a');
  });

  it('shows the asker name, with a neutral fallback for nameless rows (never the raw uuid)', async () => {
    mockList.mockResolvedValue(page([inq('a', false, 'Asha Kimani'), inq('b', true, null)]));
    renderInbox();
    await waitFor(() => expect(screen.getAllByTestId('inbox-row')).toHaveLength(2));
    expect(screen.getByText(/Asha Kimani/)).toBeInTheDocument();
    expect(screen.getByText(/Weespas user/)).toBeInTheDocument();
    expect(screen.queryByText(/buyer-uuid/)).toBeNull();
  });

  it('shows an empty state when there are no inquiries', async () => {
    mockList.mockResolvedValue(page([]));
    renderInbox();
    await waitFor(() => expect(screen.getByText(/No inquiries yet/i)).toBeInTheDocument());
  });
});
