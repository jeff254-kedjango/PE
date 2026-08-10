import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { PropertyShort } from '../api/shorts';

// TradePage's §8 Shops|Clips|Podcasts lane behaviour: Shops shows the image timeline (ProductFeed);
// tapping Clips opens the shared full-screen vertical overlay (VerticalVideoFeed) over the page;
// exiting the overlay returns to Shops; Podcasts has no backend and says so. We keep the REAL
// FeedKindToggle so we click the actual affordance, and stub the heavy children/hooks so the test
// targets the lane→overlay wiring only.

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: true, token: 'wtok', user: { name: 'Asha' } }),
}));
vi.mock('../hooks/useGeolocation', () => ({
  useGeolocation: () => ({ latitude: -1.29, longitude: 36.82, error: null, requestLocation: vi.fn() }),
}));
vi.mock('../hooks/useCommerceSession', () => ({
  useCommerceSession: () => ({
    session: { token: 'ctok', commerce_url: 'http://c' }, isLoading: false, error: null,
  }),
}));

const short: PropertyShort = {
  id: 's1', title: 'Reel', price: 50, currency: 'KES', listing_type: 'sale', category: 'shop',
  location_name: 'Shop', video: { url: '/uploads/trade/videos/a.mp4' }, is_featured: false,
};
const mockToggleLike = vi.fn();
// Mutable widen state so a test can flip the video lane into the widened branch and assert the
// honest "closest shops are within X km" note appears on the rail + overlay.
const videoWiden = { widened: false, nearestDistanceM: null as number | null, immediateCount: 0 };
vi.mock('../hooks/useCommerceVideoShorts', () => ({
  useCommerceVideoShorts: () => ({
    shorts: [short],
    sellerById: new Map([['s1', 'sel1']]),
    isLiked: () => false,
    toggleLike: mockToggleLike,
    priceLabelFor: () => 'KES 50',
    widened: videoWiden.widened,
    nearestDistanceM: videoWiden.nearestDistanceM,
    immediateCount: videoWiden.immediateCount,
  }),
}));

// Stub the heavy children — we only assert the timeline vs overlay switch.
vi.mock('../components/trade/ProductFeed', () => ({
  default: () => <div data-testid="product-feed">timeline</div>,
}));
vi.mock('../components/trade/ComposerBox', () => ({ default: () => <div data-testid="composer" /> }));
vi.mock('../components/trade/TrendingRail', () => ({ default: () => <div data-testid="trending" /> }));
vi.mock('../components/trade/ShopVideoStrip', () => ({
  default: ({ onOpenVideo }: { onOpenVideo: (id: string) => void }) => (
    <button type="button" data-testid="rail-tile" onClick={() => onOpenVideo('s1')}>tile</button>
  ),
}));
vi.mock('../components/trade/QuickBuys', () => ({ default: () => <div data-testid="quickbuys" /> }));
vi.mock('../components/trade/FlashSales', () => ({ default: () => <div data-testid="flash" /> }));
// §8 Chunk A: TradePage no longer imports <Storefront>. Tapping a seller navigates to /shop/...
// which is a separate route (ShopPage). No overlay/sheet mounted here anymore; no mock needed.
vi.mock('../components/shorts/VerticalVideoFeed', () => ({
  default: ({ initialShortId, onExit, notice }: { initialShortId?: string | null; onExit?: () => void; notice?: string | null }) => (
    <div data-testid="vertical-feed" data-initial-id={initialShortId ?? ''} data-notice={notice ?? ''}>
      <button type="button" data-testid="vf-exit" onClick={onExit}>close</button>
    </div>
  ),
}));

import TradePage from './TradePage';

function renderPage() {
  return render(<MemoryRouter initialEntries={['/trade']}><TradePage /></MemoryRouter>);
}

beforeEach(() => {
  vi.clearAllMocks();
  videoWiden.widened = false;
  videoWiden.nearestDistanceM = null;
  videoWiden.immediateCount = 0;
});
afterEach(() => { vi.restoreAllMocks(); });

describe('TradePage — Shops|Clips|Podcasts lane toggle', () => {
  it('defaults to the Shops image timeline with no video overlay', () => {
    renderPage();
    expect(screen.getByTestId('product-feed')).toBeInTheDocument();
    expect(screen.queryByTestId('vertical-feed')).toBeNull();
    expect(screen.getByTestId('kind-shops').getAttribute('aria-selected')).toBe('true');
  });

  it('tapping Clips opens the full-screen vertical overlay (starting at the top)', () => {
    renderPage();
    fireEvent.click(screen.getByTestId('kind-clips'));
    const feed = screen.getByTestId('vertical-feed');
    expect(feed).toBeInTheDocument();
    expect(feed.getAttribute('data-initial-id')).toBe(''); // toggle opens at the top
    // The timeline stays mounted underneath (Clips is an overlay, not a feed swap).
    expect(screen.getByTestId('product-feed')).toBeInTheDocument();
  });

  // Podcasts has no backend at all. It must say so and must NOT render a bare empty timeline that
  // reads as "no podcasts near you" — the absence is ours, not the neighbourhood's.
  it('Podcasts shows an honest not-live note and hides (does not unmount) the timeline', () => {
    renderPage();
    fireEvent.click(screen.getByTestId('kind-podcasts'));
    expect(screen.getByTestId('lane-podcasts-empty').textContent).toMatch(/aren’t live yet/i);
    // Still mounted (so its fetch + scroll survive a lane round-trip) but hidden from the page.
    const feed = screen.getByTestId('product-feed');
    expect(feed).toBeInTheDocument();
    expect(feed.closest('[hidden]')).not.toBeNull();
    // Podcasts is NOT the video overlay.
    expect(screen.queryByTestId('vertical-feed')).toBeNull();
  });

  it('returning from Podcasts to Shops re-shows the timeline', () => {
    renderPage();
    fireEvent.click(screen.getByTestId('kind-podcasts'));
    fireEvent.click(screen.getByTestId('kind-shops'));
    expect(screen.queryByTestId('lane-podcasts-empty')).toBeNull();
    expect(screen.getByTestId('product-feed').closest('[hidden]')).toBeNull();
  });

  it('exiting the overlay returns to Shops', () => {
    renderPage();
    fireEvent.click(screen.getByTestId('kind-clips'));
    expect(screen.getByTestId('vertical-feed')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('vf-exit'));
    expect(screen.queryByTestId('vertical-feed')).toBeNull();
    expect(screen.getByTestId('kind-shops').getAttribute('aria-selected')).toBe('true');
  });

  it('tapping a rail tile opens the overlay AT that clip', () => {
    renderPage();
    fireEvent.click(screen.getByTestId('rail-tile'));
    const feed = screen.getByTestId('vertical-feed');
    expect(feed).toBeInTheDocument();
    expect(feed.getAttribute('data-initial-id')).toBe('s1');
  });

  it('shows the honest widen note on the rail + overlay when the video lane widened', () => {
    videoWiden.widened = true;
    videoWiden.nearestDistanceM = 4200; // ~5 km ceiling
    renderPage();
    // Rail note (distance only — never a delivery claim).
    const railNote = screen.getByText(/closest shops are within 5 km/i);
    expect(railNote).toBeInTheDocument();
    expect(railNote.textContent).not.toMatch(/deliver/i);
    // Same string passed to the overlay.
    fireEvent.click(screen.getByTestId('kind-clips'));
    expect(screen.getByTestId('vertical-feed').getAttribute('data-notice')).toMatch(/within 5 km/i);
  });

  it('shows no widen note when the immediate radius had content', () => {
    renderPage();
    expect(screen.queryByText(/closest shops are within/i)).toBeNull();
    fireEvent.click(screen.getByTestId('kind-clips'));
    expect(screen.getByTestId('vertical-feed').getAttribute('data-notice')).toBe('');
  });
});
