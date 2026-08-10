import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

vi.mock('../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../api/commerce')>('../api/commerce');
  return { ...actual, getFeed: vi.fn(), toggleSave: vi.fn() };
});

import { getFeed, toggleSave, type CommerceSession, type FeedItem, type FeedResponse } from '../api/commerce';
import { useCommerceVideoShorts } from './useCommerceVideoShorts';

const mockGetFeed = vi.mocked(getFeed);
const mockToggleSave = vi.mocked(toggleSave);
const SESSION: CommerceSession = { token: 'tok', commerce_url: 'http://c' };

function item(over: Partial<FeedItem> = {}): FeedItem {
  return {
    id: 'i1', shop_id: 'sh1', seller_id: 'sel1', shop_name: 'Mama Mboga', shop_avatar_url: null,
    shop_category: null, property_uuid: null, title: 'Sukuma reel', description: null,
    price_cents: 5000, currency: 'KES', media_urls: ['/uploads/trade/videos/a.mp4'], distance_m: 800,
    score: 1, save_count: 0, saved_by_me: false, comment_count: 0, is_short_video: true, post_kind: 'product',
    seller_rating: null, seller_review_count: 0, is_promoted: false, is_sponsored: false,
    boost_tier: null, created_at: '2026-06-30T00:00:00Z',
    ...over,
  };
}

const feed = (items: FeedItem[], over: Partial<FeedResponse> = {}): FeedResponse => ({
  items,
  next_cursor: over.next_cursor ?? null,
  widened: over.widened ?? false,
  nearest_distance_m: over.nearest_distance_m ?? null,
  immediate_count: over.immediate_count ?? 0,
});

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

function renderVideoShorts(session: CommerceSession | null = SESSION) {
  return renderHook(() => useCommerceVideoShorts({ session, lat: -1.29, lng: 36.82 }), { wrapper });
}

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); });

describe('useCommerceVideoShorts', () => {
  it('keeps items carrying a video and drops image-only items; maps id→seller', async () => {
    mockGetFeed.mockResolvedValue(feed([
      item({ id: 'i1', seller_id: 'sel1', title: 'Vid one', media_urls: ['/uploads/trade/videos/a.mp4'] }),
      item({ id: 'i2', seller_id: 'sel2', title: 'Pic only', media_urls: ['/uploads/trade/images/b.jpg'] }),
      item({ id: 'i3', seller_id: 'sel3', title: 'Vid two', media_urls: ['/uploads/trade/images/x.jpg', '/uploads/trade/videos/c.webm'] }),
    ]));
    const { result } = renderVideoShorts();
    await waitFor(() => expect(result.current.shorts).toHaveLength(2));

    expect(result.current.shorts.map((s) => s.id)).toEqual(['i1', 'i3']);
    // The mixed post uses its VIDEO url (not the leading image).
    expect(result.current.shorts[1].video.url).toBe('/uploads/trade/videos/c.webm');
    expect(result.current.sellerById.get('i1')).toBe('sel1');
    expect(result.current.sellerById.get('i3')).toBe('sel3');
    expect(result.current.sellerById.has('i2')).toBe(false);
  });

  it('priceLabelFor formats KES from the short price', async () => {
    mockGetFeed.mockResolvedValue(feed([item({ id: 'i1', price_cents: 50000, media_urls: ['/uploads/trade/videos/a.mp4'] })]));
    const { result } = renderVideoShorts();
    await waitFor(() => expect(result.current.shorts).toHaveLength(1));
    // formatPrice(500, 'KES') → "KES 500" (sub-1000 renders in full).
    const label = result.current.priceLabelFor(result.current.shorts[0]);
    expect(label).toMatch(/500/);
    expect(label).toMatch(/KES|KSh/i);
  });

  it('toggleLike is optimistic then reconciles to the server truth', async () => {
    mockGetFeed.mockResolvedValue(feed([item({ id: 'i1', media_urls: ['/uploads/trade/videos/a.mp4'] })]));
    mockToggleSave.mockResolvedValue({ listing_id: 'i1', saved: true, save_count: 1 });
    const { result } = renderVideoShorts();
    await waitFor(() => expect(result.current.shorts).toHaveLength(1));

    expect(result.current.isLiked('i1')).toBe(false);
    act(() => { result.current.toggleLike('i1'); });
    // Optimistic flip is immediate.
    expect(result.current.isLiked('i1')).toBe(true);
    await waitFor(() => expect(mockToggleSave).toHaveBeenCalledWith(SESSION, 'i1'));
    // Reconciled — server says saved:true, so it stays on.
    await waitFor(() => expect(result.current.isLiked('i1')).toBe(true));
  });

  it('toggleLike rolls back the optimistic flip when the save fails', async () => {
    mockGetFeed.mockResolvedValue(feed([item({ id: 'i1', media_urls: ['/uploads/trade/videos/a.mp4'] })]));
    mockToggleSave.mockRejectedValue(new Error('network'));
    const { result } = renderVideoShorts();
    await waitFor(() => expect(result.current.shorts).toHaveLength(1));

    act(() => { result.current.toggleLike('i1'); });
    expect(result.current.isLiked('i1')).toBe(true); // optimistic
    await waitFor(() => expect(result.current.isLiked('i1')).toBe(false)); // rolled back
  });

  it('does not fetch without a session', () => {
    renderVideoShorts(null);
    expect(mockGetFeed).not.toHaveBeenCalled();
  });

  it('surfaces the auto-widen signals from the first feed page', async () => {
    mockGetFeed.mockResolvedValue(feed(
      [item({ id: 'i1', media_urls: ['/uploads/trade/videos/a.mp4'] })],
      { widened: true, nearest_distance_m: 4200 },
    ));
    const { result } = renderVideoShorts();
    await waitFor(() => expect(result.current.shorts).toHaveLength(1));
    expect(result.current.widened).toBe(true);
    expect(result.current.nearestDistanceM).toBe(4200);
  });

  it('defaults the widen signals to not-widened when the radius had content', async () => {
    mockGetFeed.mockResolvedValue(feed([item({ id: 'i1', media_urls: ['/uploads/trade/videos/a.mp4'] })]));
    const { result } = renderVideoShorts();
    await waitFor(() => expect(result.current.shorts).toHaveLength(1));
    expect(result.current.widened).toBe(false);
    expect(result.current.nearestDistanceM).toBeNull();
  });
});
