import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { FeedItem, CommerceSession } from '../../api/commerce';

// ProductFeed wires useCommerceFeed → ProductCard grid and surfaces the honest auto-widen note.
// We stub the feed hook (so no network) and the Confirmed-shield batch, then drive the widen
// signals to assert the note text — including that it reports DISTANCE ONLY (never delivery).
const feedState = {
  items: [] as FeedItem[],
  isLoading: false,
  isError: false,
  error: null as Error | null,
  fetchNextPage: vi.fn(),
  hasNextPage: false,
  isFetchingNextPage: false,
  widened: false,
  nearestDistanceM: null as number | null,
  immediateCount: 0,
};

vi.mock('../../hooks/useCommerceFeed', () => ({
  useCommerceFeed: () => feedState,
}));
vi.mock('../../hooks/useConfirmedListings', () => ({
  useConfirmedListings: () => new Set<string>(),
}));
// ProductCard is covered by its own suite; stub it to a marker so this suite isolates the feed
// wrapper (states + widen note) without dragging in the card's engagement/media machinery.
vi.mock('./ProductCard', () => ({
  default: ({ item }: { item: FeedItem }) => <div data-testid="product-card">{item.title}</div>,
}));

import ProductFeed from './ProductFeed';

const SESSION: CommerceSession = { token: 'tok', commerce_url: 'http://c' };

function makeItem(over: Partial<FeedItem> = {}): FeedItem {
  return {
    id: 'l1', shop_id: 's1', seller_id: 'sel1', shop_name: 'Mama Njeri', shop_avatar_url: null,
    shop_category: null, property_uuid: null, title: 'Sukuma', description: null, price_cents: 2000,
    currency: 'KES', media_urls: [], distance_m: 320, score: 0.5, save_count: 0, saved_by_me: false,
    comment_count: 0, is_short_video: false, post_kind: 'product', seller_rating: null,
    seller_review_count: 0, is_promoted: false, is_sponsored: false, boost_tier: null,
    created_at: '2026-06-29T10:00:00Z', ...over,
  };
}

function renderFeed() {
  return render(
    <ProductFeed session={SESSION} lat={-1.29} lng={36.82} onSelectSeller={() => {}} />,
  );
}

beforeEach(() => {
  feedState.items = [];
  feedState.isLoading = false;
  feedState.isError = false;
  feedState.widened = false;
  feedState.nearestDistanceM = null;
  feedState.immediateCount = 0;
});

describe('ProductFeed — auto-widen note', () => {
  it('shows the EMPTY-branch note when the immediate radius was empty (immediateCount 0)', () => {
    feedState.items = [makeItem({ title: 'Five km sukuma' })];
    feedState.widened = true;
    feedState.nearestDistanceM = 4300; // 4.3 km → ceils UP to "within 5 km"
    feedState.immediateCount = 0;
    renderFeed();
    expect(screen.getByText(/nothing selling in your immediate area/i)).toBeInTheDocument();
    expect(screen.getByText(/closest shops are within 5 km/i)).toBeInTheDocument();
    // Honesty contract: the note must NEVER claim delivery (the platform has no such capability).
    expect(screen.queryByText(/delivery/i)).toBeNull();
    expect(screen.getByTestId('product-card')).toBeInTheDocument();
  });

  it('shows the SPARSE-branch note (never claims emptiness) when a few were local (immediateCount > 0)', () => {
    feedState.items = [makeItem({ title: 'Near sukuma' }), makeItem({ id: 'l2', title: 'Far sukuma' })];
    feedState.widened = true;
    feedState.nearestDistanceM = 300;
    feedState.immediateCount = 1; // one local item — the copy must NOT say "nothing"
    renderFeed();
    expect(screen.getByText(/only a few sellers nearby/i)).toBeInTheDocument();
    expect(screen.getByText(/also showing shops within 1 km/i)).toBeInTheDocument();
    expect(screen.queryByText(/nothing selling in your immediate area/i)).toBeNull();
    expect(screen.queryByText(/delivery/i)).toBeNull();
  });

  it('shows NO widen note when the immediate radius had content', () => {
    feedState.items = [makeItem()];
    feedState.widened = false;
    feedState.nearestDistanceM = 120;
    renderFeed();
    expect(screen.queryByText(/closest shops are within/i)).toBeNull();
    expect(screen.getByTestId('product-card')).toBeInTheDocument();
  });

  it('shows the truly-empty state (no widen note, no fabricated distance) when nothing is found', () => {
    feedState.items = [];
    feedState.widened = false;
    feedState.nearestDistanceM = null;
    renderFeed();
    expect(screen.getByText(/nothing on sale near you yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/closest shops are within/i)).toBeNull();
  });
});
