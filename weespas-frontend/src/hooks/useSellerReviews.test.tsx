import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

vi.mock('../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../api/commerce')>('../api/commerce');
  return { ...actual, getSellerReviews: vi.fn() };
});

import {
  getSellerReviews,
  type CommerceSession,
  type ReviewOut,
  type SellerReviewsPage,
} from '../api/commerce';
import { useSellerReviews } from './useSellerReviews';

const mockGetSellerReviews = vi.mocked(getSellerReviews);
const SESSION: CommerceSession = { token: 'tok', commerce_url: 'http://c' };
const SELLER = 'sel-1';

// A concise review factory — every field required by ReviewOut is populated with a plausible
// default so a test only spells out what actually matters.
function review(over: Partial<ReviewOut> = {}): ReviewOut {
  return {
    id: 'r1',
    order_id: 'o1',
    seller_id: SELLER,
    listing_id: 'l1',
    reviewer_uuid: 'u-buyer-1',
    rating: 5,
    body: null,
    created_at: '2026-07-01T00:00:00Z',
    ...over,
  };
}

const page = (items: ReviewOut[], over: Partial<SellerReviewsPage> = {}): SellerReviewsPage => ({
  summary: over.summary ?? { average: items.length ? 4.5 : null, count: items.length },
  items,
  next_cursor: over.next_cursor ?? null,
});

function wrapper({ children }: { children: React.ReactNode }) {
  // retry:false so a rejected-mock test doesn't wait through the default backoff.
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

function renderReviews(session: CommerceSession | null = SESSION, sellerId: string | null = SELLER) {
  return renderHook(() => useSellerReviews(session, sellerId), { wrapper });
}

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); });

describe('useSellerReviews', () => {
  it('returns the first page items and the aggregate summary', async () => {
    mockGetSellerReviews.mockResolvedValue(page(
      [review({ id: 'r1', rating: 5 }), review({ id: 'r2', rating: 4 })],
      { summary: { average: 4.5, count: 2 } },
    ));
    const { result } = renderReviews();
    await waitFor(() => expect(result.current.items).toHaveLength(2));
    expect(result.current.items.map((r) => r.id)).toEqual(['r1', 'r2']);
    expect(result.current.summary).toEqual({ average: 4.5, count: 2 });
    // The cursor arg on the first call is null (initialPageParam).
    expect(mockGetSellerReviews).toHaveBeenCalledWith(SESSION, SELLER, { cursor: null });
  });

  it('advances the cursor on fetchNextPage and concatenates newest-first', async () => {
    // Page 1: two reviews, cursor 'c1' present ⇒ hasNextPage true.
    // Page 2: one older review, cursor null ⇒ end of feed.
    mockGetSellerReviews
      .mockResolvedValueOnce(page(
        [review({ id: 'r1' }), review({ id: 'r2' })],
        { summary: { average: 4.5, count: 3 }, next_cursor: 'c1' },
      ))
      .mockResolvedValueOnce(page(
        [review({ id: 'r3' })],
        { summary: { average: 4.5, count: 3 } },
      ));

    const { result } = renderReviews();
    await waitFor(() => expect(result.current.items).toHaveLength(2));
    expect(result.current.hasNextPage).toBe(true);

    // act() with a value-returning async callback keeps us on the non-deprecated overload
    // (@testing-library/react 16+ deprecates the void-returning form). We don't need the return
    // value here — the assertion below is on the flattened `items` — but the shape matters.
    await act(() => result.current.fetchNextPage().then(() => undefined));
    await waitFor(() => expect(result.current.items).toHaveLength(3));
    expect(result.current.items.map((r) => r.id)).toEqual(['r1', 'r2', 'r3']);

    // The cursor from page 1 was passed to the page-2 request.
    expect(mockGetSellerReviews).toHaveBeenNthCalledWith(2, SESSION, SELLER, { cursor: 'c1' });
    // The summary stays anchored to the FIRST page (later pages don't clobber the header).
    expect(result.current.summary).toEqual({ average: 4.5, count: 3 });
  });

  it('renders an "unrated" summary immediately (before the first page lands) and matches the server\'s empty shape', async () => {
    // A brand-new / unrated seller: the server returns an empty page with count:0, average:null.
    mockGetSellerReviews.mockResolvedValue(page([], { summary: { average: null, count: 0 } }));
    const { result } = renderReviews();
    // Sentinel is visible synchronously (before the first fetch settles) so the header renders now.
    expect(result.current.summary).toEqual({ average: null, count: 0 });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    // After the fetch settles, the same shape is returned by the server — indistinguishable in the UI.
    expect(result.current.items).toEqual([]);
    expect(result.current.summary).toEqual({ average: null, count: 0 });
  });

  it('does not fetch without a session', () => {
    renderReviews(null, SELLER);
    expect(mockGetSellerReviews).not.toHaveBeenCalled();
  });

  it('does not fetch without a sellerId', () => {
    renderReviews(SESSION, null);
    expect(mockGetSellerReviews).not.toHaveBeenCalled();
  });

  it('surfaces network errors on isError', async () => {
    mockGetSellerReviews.mockRejectedValue(new Error('network down'));
    const { result } = renderReviews();
    // The hook keeps `retry: 1` (a single retry, matching prod resilience) even under the test
    // client's `retry: false` default — per-query options win in react-query v5. So the query
    // makes TWO attempts before isError flips; give waitFor a longer window than its 1s default
    // so the retry has room to run before we assert.
    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 3000 });
    expect(result.current.error?.message).toBe('network down');
    // The sentinel summary is still readable in the error state (the header degrades to "unrated"
    // rather than showing nothing).
    expect(result.current.summary).toEqual({ average: null, count: 0 });
    // Two attempts fired (first + one retry) — matches the hook's `retry: 1`.
    expect(mockGetSellerReviews).toHaveBeenCalledTimes(2);
  });
});
