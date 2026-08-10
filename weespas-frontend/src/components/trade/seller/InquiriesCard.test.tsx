import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../../api/commerce')>('../../../api/commerce');
  return { ...actual, getMyInquiries: vi.fn(), markInquiryRead: vi.fn() };
});

// Toast context is a peer dependency of InquiriesInbox — provide a benign mock.
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }),
}));

import { getMyInquiries, type CommerceSession, type InquiryPage } from '../../../api/commerce';
import InquiriesCard from './InquiriesCard';

const mockList = vi.mocked(getMyInquiries);

const SESSION: CommerceSession = { token: 'ctok', commerce_url: 'http://c' };

function page(items: Partial<InquiryPage['items'][number]>[]): InquiryPage {
  return {
    items: items.map((it, i) => ({
      id: it.id ?? `inq-${i}`,
      listing_id: it.listing_id ?? 'lst1',
      listing_title: it.listing_title ?? `Item ${i}`,
      seller_id: it.seller_id ?? 'seller-1',
      from_user_uuid: it.from_user_uuid ?? `u-${i}`,
      from_user_name: it.from_user_name ?? 'A Buyer',
      message: it.message ?? 'Is this available?',
      is_read: it.is_read ?? false,
      created_at: it.created_at ?? '2026-08-05T09:00:00Z',
    })),
    next_cursor: null,
  };
}

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <InquiriesCard session={SESSION} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('InquiriesCard', () => {
  it('renders the card title', async () => {
    mockList.mockResolvedValue(page([]));
    renderCard();
    // Wait for the initial load to settle (the title is present regardless, but this makes the
    // negative-badge assertion below unambiguous).
    await screen.findByText(/No inquiries yet/i);
    expect(screen.getByRole('heading', { name: /^Inquiries$/ })).toBeInTheDocument();
  });

  it('shows the unread badge when there are unread inquiries', async () => {
    mockList.mockResolvedValue(page([
      { id: 'a', is_read: false },
      { id: 'b', is_read: false },
      { id: 'c', is_read: true },
    ]));
    renderCard();
    expect(await screen.findByLabelText(/2 unread/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/2 unread/i)).toHaveTextContent('(2)');
  });

  it('collapses the counter to "9+" when unread exceeds 9', async () => {
    mockList.mockResolvedValue(page(
      Array.from({ length: 12 }, (_, i) => ({ id: `id-${i}`, is_read: false })),
    ));
    renderCard();
    expect(await screen.findByLabelText(/12 unread/i)).toHaveTextContent('(9+)');
  });

  it('hides the badge when there are no unread inquiries', async () => {
    mockList.mockResolvedValue(page([{ id: 'a', is_read: true }]));
    renderCard();
    // The inbox renders one row (marked read) — wait for it and then confirm no unread badge.
    await screen.findByTestId('inbox-row');
    expect(screen.queryByLabelText(/unread/i)).not.toBeInTheDocument();
  });

  it('renders the inbox rows through the wrapped InquiriesInbox', async () => {
    mockList.mockResolvedValue(page([{ id: 'a', listing_title: 'Kikoi tote bag', is_read: false }]));
    renderCard();
    expect(await screen.findByText(/Kikoi tote bag/i)).toBeInTheDocument();
  });
});
