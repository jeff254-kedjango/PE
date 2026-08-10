import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import type { PropertyShort } from '../../api/shorts';
import ShopVideoStrip from './ShopVideoStrip';

// ShopVideoStrip is now a THIN presentational shelf: it takes pre-adapted shorts + a price labeller
// and asks the PAGE to open the shared vertical player (onOpenVideo). The commerce→shorts adaptation
// and SAVE state live in useCommerceVideoShorts (covered by its own test). So this suite renders the
// REUSED ShortsShelf and asserts the tile/nav/empty behaviour and the onOpenVideo wiring — no feed
// fetch, no overlay here.

function short(over: Partial<PropertyShort> = {}): PropertyShort {
  return {
    id: 'i1', title: 'Vid one', price: 50, currency: 'KES',
    listing_type: 'sale', category: 'shop', agent_name: 'Mama Mboga',
    location_name: 'Mama Mboga', main_image: undefined,
    video: { url: '/uploads/trade/videos/a.mp4' }, is_featured: false,
    ...over,
  };
}

const priceLabelFor = (s: PropertyShort) => `KES ${s.price}`;

function renderStrip(shorts: PropertyShort[], onOpenVideo = vi.fn()) {
  const utils = render(
    <ShopVideoStrip shorts={shorts} priceLabelFor={priceLabelFor} onOpenVideo={onOpenVideo} />,
  );
  return { ...utils, onOpenVideo };
}

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
  // jsdom has no ResizeObserver — the REUSED ShortsShelf observes its track for arrow-state.
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ShopVideoStrip (thin shelf that reuses ShortsShelf)', () => {
  it('renders the REUSED ShortsShelf with a poster tile per short', () => {
    const { container } = renderStrip([
      short({ id: 'i1', title: 'Vid one' }),
      short({ id: 'i3', title: 'Vid two', video: { url: '/uploads/trade/videos/c.webm' } }),
    ]);
    expect(container.querySelector('.shorts-shelf.shop-video-strip')).toBeInTheDocument();
    expect(container.querySelectorAll('.short-card')).toHaveLength(2);
    expect(screen.getByText('Vid one')).toBeInTheDocument();
    expect(screen.getByText('Vid two')).toBeInTheDocument();
  });

  it('shows the bottom-right chevron nav from the reused shelf', () => {
    renderStrip([
      short({ id: 'i1' }),
      short({ id: 'i2', video: { url: '/uploads/trade/videos/b.mp4' } }),
    ]);
    expect(screen.getByLabelText('Scroll shorts left')).toBeInTheDocument();
    expect(screen.getByLabelText('Scroll shorts right')).toBeInTheDocument();
  });

  it('has NO card chrome — hideHeader suppresses the eyebrow + See-all header', () => {
    const { container } = renderStrip([short({ id: 'i1' })]);
    expect(container.querySelector('.shorts-shelf.shop-video-strip')).toBeInTheDocument();
    expect(container.querySelector('.shorts-shelf__header')).toBeNull();
  });

  it('tapping a tile asks the page to open the vertical feed at that video id', () => {
    const { container, onOpenVideo } = renderStrip([
      short({ id: 'i1', title: 'First' }),
      short({ id: 'i2', title: 'Second', video: { url: '/uploads/trade/videos/b.mp4' } }),
    ]);
    const cards = container.querySelectorAll<HTMLElement>('.short-card');
    fireEvent.click(within(cards[1] as HTMLElement).getByText('Second'));
    expect(onOpenVideo).toHaveBeenCalledWith('i2');
  });

  // Chunk 1 (permanent columns): the strip STAYS MOUNTED with a placeholder when there are no
  // clips nearby, so the right column keeps its width on load. The reused ShortsShelf is NOT
  // invoked in the empty state — its own copy is aimed at real-estate ("No videos in this area")
  // and its chevron nav row would look broken with no tiles behind it.
  it('stays mounted with a placeholder when there are no shorts', () => {
    const { container } = renderStrip([]);
    // ShortsShelf is not used in the empty state (that shelf's own copy is real-estate-flavoured).
    expect(container.querySelector('.shorts-shelf')).toBeNull();
    // The empty variant IS mounted so the column holds its width.
    expect(screen.getByTestId('shop-video-strip-empty')).toBeInTheDocument();
    // Honest "no clips nearby" placeholder is shown once loading resolves (isLoading defaults to
    // false in these tests, so we see the placeholder rather than the pure-skeleton state).
    expect(screen.getByTestId('shop-video-strip-placeholder')).toBeInTheDocument();
    expect(screen.getByText(/No clips nearby yet/i)).toBeInTheDocument();
  });

  // Chunk 1: while the initial fetch is in flight and shorts is still empty, the strip renders
  // ONLY the shimmering skeleton tiles — no placeholder text (which would look like an
  // authoritative "no clips" answer before the server has spoken).
  it('shows skeleton tiles (no placeholder text) while loading with no shorts yet', () => {
    render(
      <ShopVideoStrip shorts={[]} priceLabelFor={priceLabelFor} onOpenVideo={vi.fn()} isLoading />,
    );
    expect(screen.getByTestId('shop-video-strip-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('shop-video-strip-placeholder')).toBeNull();
  });
});
