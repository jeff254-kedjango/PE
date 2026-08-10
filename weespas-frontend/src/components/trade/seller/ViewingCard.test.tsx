import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../../api/commerce')>('../../../api/commerce');
  return {
    ...actual,
    getShopLiveViewers: vi.fn(),
    getShopViewHistory: vi.fn(),
    promoteAllShopListings: vi.fn(),
  };
});

import {
  getShopLiveViewers,
  getShopViewHistory,
  promoteAllShopListings,
  type CommerceSession,
  type ShopOut,
  type LiveViewerOut,
  type LiveViewersOut,
  type ViewHistoryOut,
  type PromoteAllOut,
} from '../../../api/commerce';
import ViewingCard from './ViewingCard';

const mockLive = vi.mocked(getShopLiveViewers);
const mockHistory = vi.mocked(getShopViewHistory);
const mockPromote = vi.mocked(promoteAllShopListings);

const SESSION: CommerceSession = { token: 'ctok', commerce_url: 'http://c' };
const SHOP: ShopOut = {
  id: 'shop1',
  seller_id: 'sel1',
  name: 'Shop A',
  handle: null,
  category: null,
  banner_url: null,
  avatar_url: null,
  description: null,
  lat: -1.29,
  lng: 36.82,
  distance_m: null,
  follower_count: 0,
  following: false,
  is_public: true,
  created_at: '2026-06-29T00:00:00Z',
} as unknown as ShopOut;

function viewer(overrides: Partial<LiveViewerOut> = {}): LiveViewerOut {
  return {
    session_id: overrides.session_id ?? 's1',
    viewer_uuid: overrides.viewer_uuid ?? 'user-1',
    display_name: overrides.display_name ?? 'Alice',
    avatar_url: overrides.avatar_url ?? null,
    phone: overrides.phone ?? null,
    area_label: overrides.area_label ?? 'Kilimani',
    viewing_listing_id: overrides.viewing_listing_id ?? null,
    viewing_listing_title: overrides.viewing_listing_title ?? null,
    last_heartbeat_at: overrides.last_heartbeat_at ?? '2026-08-04T09:00:30Z',
  };
}

function liveOut(items: LiveViewerOut[] = []): LiveViewersOut {
  return { shop_id: SHOP.id, count: items.length, window_seconds: 60, items };
}

function historyOut(items: Partial<ViewHistoryOut['items'][number]>[] = [], next: string | null = null): ViewHistoryOut {
  return {
    items: items.map((i, idx) => ({
      id: i.id ?? `evt${idx}`,
      viewer_uuid: i.viewer_uuid ?? null,
      session_id: i.session_id ?? `sess${idx}`,
      viewed_at: i.viewed_at ?? '2026-08-04T09:00:00Z',
      last_heartbeat_at: i.last_heartbeat_at ?? '2026-08-04T09:00:30Z',
    })),
    next_cursor: next,
  };
}

function renderCard(shop: ShopOut | null = SHOP) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ViewingCard session={SESSION} shop={shop} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockLive.mockResolvedValue(liveOut([viewer({ session_id: 's1', display_name: 'Alice' })]));
  mockHistory.mockResolvedValue(historyOut());
  mockPromote.mockResolvedValue({
    shop_id: SHOP.id,
    promoted_count: 3,
    skipped_ids: [],
    duration_seconds: 7200,
    expires_at: '2026-08-04T11:00:00Z',
  } satisfies PromoteAllOut);
});

describe('ViewingCard — live tab (C+)', () => {
  it('renders each hydrated viewer as a row with name and area', async () => {
    mockLive.mockResolvedValue(liveOut([
      viewer({ session_id: 's1', display_name: 'Alice', area_label: 'Kilimani' }),
      viewer({ session_id: 's2', display_name: 'Bob', area_label: 'CBD' }),
    ]));
    renderCard();
    const rows = await screen.findAllByTestId('viewing-card-viewer-row');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent(/Alice/);
    expect(rows[0]).toHaveTextContent(/Kilimani/);
    expect(rows[1]).toHaveTextContent(/Bob/);
    expect(rows[1]).toHaveTextContent(/CBD/);
  });

  it('shows the (N) counter next to the header', async () => {
    mockLive.mockResolvedValue(liveOut([
      viewer({ session_id: 's1' }),
      viewer({ session_id: 's2' }),
      viewer({ session_id: 's3' }),
    ]));
    renderCard();
    expect(await screen.findByLabelText(/3 live/i)).toBeInTheDocument();
  });

  it('shows "browsing storefront" when the viewer is not on a PDP', async () => {
    mockLive.mockResolvedValue(liveOut([
      viewer({ session_id: 's1', viewing_listing_id: null, viewing_listing_title: null }),
    ]));
    renderCard();
    expect(await screen.findByText(/browsing storefront/i)).toBeInTheDocument();
  });

  it('shows "viewing X" when the viewer is on a listing', async () => {
    mockLive.mockResolvedValue(liveOut([
      viewer({ session_id: 's1', viewing_listing_id: 'lst-1', viewing_listing_title: 'Kikoi tote bag' }),
    ]));
    renderCard();
    expect(await screen.findByText(/viewing Kikoi tote bag/i)).toBeInTheDocument();
  });

  it('renders a clickable phone link ONLY when the payload exposes phone', async () => {
    mockLive.mockResolvedValue(liveOut([
      viewer({ session_id: 's1', display_name: 'Alice', phone: '+254700000000' }),
      viewer({ session_id: 's2', display_name: 'Bob', phone: null }),
    ]));
    renderCard();
    await screen.findAllByTestId('viewing-card-viewer-row');
    const phoneLink = screen.getByRole('link', { name: '+254700000000' });
    expect(phoneLink).toHaveAttribute('href', 'tel:+254700000000');
    // Bob (no phone) has no tel link.
    expect(screen.queryByRole('link', { name: /Bob/ })).not.toBeInTheDocument();
  });

  it('falls back to a monogram avatar when no avatar_url is present', async () => {
    mockLive.mockResolvedValue(liveOut([viewer({ session_id: 's1', display_name: 'Zebra', avatar_url: null })]));
    renderCard();
    await screen.findByTestId('viewing-card-viewer-row');
    // The fallback contains the uppercased initial.
    expect(screen.getByText('Z')).toBeInTheDocument();
  });

  it('shows the empty state when no one is viewing', async () => {
    mockLive.mockResolvedValue(liveOut([]));
    renderCard();
    expect(await screen.findByText(/No one.s viewing right now/i)).toBeInTheDocument();
  });

  it('shows an error state when the fetch fails', async () => {
    mockLive.mockRejectedValue(new Error('boom'));
    renderCard();
    expect(await screen.findByText(/Couldn.t load live viewers/i, {}, { timeout: 3000 })).toBeInTheDocument();
  });
});

describe('ViewingCard — history tab', () => {
  it('switches to history when the tab is clicked', async () => {
    mockHistory.mockResolvedValue(historyOut([
      { id: 'e1', viewer_uuid: null, session_id: 's1', viewed_at: '2026-08-04T09:00:00Z' },
      { id: 'e2', viewer_uuid: 'user-42', session_id: 's2', viewed_at: '2026-08-03T09:00:00Z' },
    ]));
    renderCard();
    await screen.findAllByTestId('viewing-card-viewer-row');
    fireEvent.click(screen.getByRole('tab', { name: /history/i }));
    expect(await screen.findByText(/Registered visitor/i)).toBeInTheDocument();
    expect(screen.getByText(/Guest/i)).toBeInTheDocument();
    expect(screen.queryByText(/user-42/)).not.toBeInTheDocument();
  });

  it('shows "no visits" empty state when the range is empty', async () => {
    mockHistory.mockResolvedValue(historyOut([]));
    renderCard();
    await screen.findAllByTestId('viewing-card-viewer-row');
    fireEvent.click(screen.getByRole('tab', { name: /history/i }));
    expect(await screen.findByText(/No visits in this range/i)).toBeInTheDocument();
  });

  it('passes since/until filters when the calendar changes', async () => {
    renderCard();
    await screen.findAllByTestId('viewing-card-viewer-row');
    fireEvent.click(screen.getByRole('tab', { name: /history/i }));
    fireEvent.change(screen.getByLabelText(/View history start date/i), { target: { value: '2026-08-01' } });
    await waitFor(() => {
      expect(mockHistory).toHaveBeenCalledWith(SESSION, SHOP.id, expect.objectContaining({
        since: expect.stringContaining('2026-08-01T00:00:00'),
      }));
    });
  });
});

describe('ViewingCard — promote button', () => {
  it('fires promoteAllShopListings with the 2h duration on click', async () => {
    renderCard();
    await screen.findAllByTestId('viewing-card-viewer-row');
    fireEvent.click(screen.getByRole('button', { name: /Promote my shop/i }));
    await waitFor(() => {
      expect(mockPromote).toHaveBeenCalledWith(SESSION, SHOP.id, 7200);
    });
    expect(await screen.findByText(/Boosted 3 listings for 2 hours/i)).toBeInTheDocument();
  });

  it('surfaces "No active in-stock listings" when nothing was promoted', async () => {
    mockPromote.mockResolvedValue({
      shop_id: SHOP.id,
      promoted_count: 0,
      skipped_ids: [],
      duration_seconds: 7200,
      expires_at: '2026-08-04T11:00:00Z',
    } satisfies PromoteAllOut);
    renderCard();
    await screen.findAllByTestId('viewing-card-viewer-row');
    fireEvent.click(screen.getByRole('button', { name: /Promote my shop/i }));
    expect(await screen.findByText(/No active in-stock listings/i)).toBeInTheDocument();
  });

  it('surfaces error message when the mutation fails', async () => {
    mockPromote.mockRejectedValue(new Error('quota exhausted'));
    renderCard();
    await screen.findAllByTestId('viewing-card-viewer-row');
    fireEvent.click(screen.getByRole('button', { name: /Promote my shop/i }));
    expect(await screen.findByText(/Couldn.t promote: quota exhausted/i)).toBeInTheDocument();
  });
});

describe('ViewingCard — no shop', () => {
  it('renders the empty hint when shop is null', () => {
    renderCard(null);
    expect(screen.getByText(/Open a shop to see who.s viewing/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Promote my shop/i })).not.toBeInTheDocument();
  });
});
