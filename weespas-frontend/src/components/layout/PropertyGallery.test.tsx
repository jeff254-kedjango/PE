import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import PropertyGallery from './PropertyGallery';
import type { Property } from '../../types/propertyApi';

vi.mock('../../api/properties', () => ({
  fetchFeaturedProperties: vi.fn(),
}));
import { fetchFeaturedProperties } from '../../api/properties';
const mockFetch = vi.mocked(fetchFeaturedProperties);

function makeListings(n: number): Property[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `P${i + 1}`,
    title: `Listing ${i + 1}`,
    price: 1_000_000 + i,
    currency: 'KES',
  })) as Property[];
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.runOnlyPendingTimers(); vi.useRealTimers(); vi.clearAllMocks(); });

async function renderResolved(listings: Property[]) {
  mockFetch.mockResolvedValue(listings);
  render(<PropertyGallery selectedPropertyId={null} onSelect={() => {}} />);
  // Flush the fetch promise + the loading→loaded commit under fake timers.
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

// The track's per-slide travel is a single scalar exposed as the `--track-offset`
// CSS custom property (the CSS decides whether it drives translateY on the desktop
// right rail or translateX on the mobile strip). Read it directly rather than
// parsing `style.transform`, which is now a static `translateY(var(--track-offset))`.
const STEP = 94; // 84px square tile + 10px gap — must match CARD_SIZE + CARD_GAP.
const trackOffset = (track: HTMLElement) => track.style.getPropertyValue('--track-offset');

describe('PropertyGallery featured carousel', () => {
  it('renders ALL featured listings as cards but WINDOWS the dots to ≤10', async () => {
    await renderResolved(makeListings(15));
    // All 15 listings still cycle: the track holds 15 real + 15 cloned cards.
    expect(document.querySelectorAll('.carousel-item')).toHaveLength(30);
    // …but the dot strip is capped at 10 so a big set can't flood the hero.
    expect(screen.getAllByRole('tab')).toHaveLength(10);
    // Fetch is called WITHOUT a hardcoded limit (undefined → backend returns all).
    expect(mockFetch).toHaveBeenCalledWith(undefined, undefined);
  });

  it('shows one dot per listing (no windowing) when count ≤ 10', async () => {
    await renderResolved(makeListings(7));
    expect(screen.getAllByRole('tab')).toHaveLength(7);
    expect(document.querySelector('.carousel-dot--edge')).toBeNull();
  });

  it('keeps the active dot inside the window as the carousel advances', async () => {
    await renderResolved(makeListings(20));
    // Jump near the end via a dot, then confirm an active dot is rendered (the
    // window slid to include it) — i.e. the active card is never orphaned.
    const lastVisible = screen.getAllByRole('tab').pop() as HTMLElement;
    await act(async () => { fireEvent.click(lastVisible); });
    expect(document.querySelector('.carousel-dot.active')).toBeTruthy();
    // Still capped at 10 dots after sliding.
    expect(screen.getAllByRole('tab')).toHaveLength(10);
  });

  it('duplicates the track for a seamless loop (clones are aria-hidden)', async () => {
    await renderResolved(makeListings(3));
    // 3 real + 3 cloned cards = 6 carousel items; clones are aria-hidden
    // (so getByRole-with-name, which skips hidden nodes, would only see 3).
    const cards = document.querySelectorAll('.carousel-item');
    expect(cards).toHaveLength(6);
    const hidden = Array.from(cards).filter((c) => c.getAttribute('aria-hidden') === 'true');
    expect(hidden).toHaveLength(3);
  });

  it('does NOT render the clickable strip for a single listing (hero shows it)', async () => {
    await renderResolved(makeListings(1));
    // A lone listing already fills the hero, so the strip (and its clones/autoplay)
    // is suppressed entirely — no redundant one-tile rail.
    expect(document.querySelector('.carousel-rail')).toBeNull();
    expect(document.querySelectorAll('.carousel-item')).toHaveLength(0);
    // The hero still renders it.
    expect(screen.getByRole('heading', { name: 'Listing 1' })).toBeTruthy();
  });

  it('autoplay advances the highlighted card forward', async () => {
    await renderResolved(makeListings(3));
    const track = document.querySelector('.carousel-track') as HTMLElement;
    expect(trackOffset(track)).toBe('0px');   // -0 normalizes to 0
    // One autoplay tick (4s) → track slides one step (84 + 10 = 94px).
    await act(async () => { vi.advanceTimersByTime(4000); });
    expect(trackOffset(track)).toBe(`${-STEP}px`);
  });

  it('does NOT navigate on arrow keys (no global keyboard hijack while scrolling)', async () => {
    await renderResolved(makeListings(3));
    const track = document.querySelector('.carousel-track') as HTMLElement;
    expect(trackOffset(track)).toBe('0px');
    // Arrow keys anywhere on the document must leave the gallery untouched — it only
    // responds to explicit prev/next chevrons, tile clicks, touch swipe and autoplay.
    for (const key of ['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp']) {
      await act(async () => { fireEvent.keyDown(window, { key }); });
      expect(trackOffset(track)).toBe('0px');
    }
  });

  it('loops seamlessly past the boundary instead of running off-screen (the disappear bug)', async () => {
    await renderResolved(makeListings(3));
    const track = document.querySelector('.carousel-track') as HTMLElement;

    // Advance through all 3 cards into the clone boundary (pos === count === 3).
    for (let i = 0; i < 3; i++) {
      await act(async () => { vi.advanceTimersByTime(4000); });
    }
    expect(trackOffset(track)).toBe(`${-3 * STEP}px`);

    // The boundary snap fires on transitionend; simulate it (jsdom doesn't run
    // CSS transitions). jsdom drops `propertyName` from fireEvent.transitionEnd,
    // so build the event explicitly to exercise the real `propertyName` guard.
    // It must reset to a REAL on-screen position (0), not stay pinned at the
    // clone where the next tick would run away into blank track.
    await act(async () => {
      const ev = new Event('transitionend', { bubbles: true });
      Object.defineProperty(ev, 'propertyName', { value: 'transform' });
      fireEvent(track, ev);
    });
    expect(trackOffset(track)).toBe('0px');

    // And the very next autoplay tick advances forward again (loop continues).
    await act(async () => { vi.advanceTimersByTime(4000); });
    expect(trackOffset(track)).toBe(`${-STEP}px`);
  });

  it('recovers via the timeout fallback if the boundary transitionend is dropped', async () => {
    await renderResolved(makeListings(2));
    const track = document.querySelector('.carousel-track') as HTMLElement;
    // Drive to the boundary (pos === 2) WITHOUT firing transitionend.
    for (let i = 0; i < 2; i++) {
      await act(async () => { vi.advanceTimersByTime(4000); });
    }
    expect(trackOffset(track)).toBe(`${-2 * STEP}px`);
    // The 650ms fallback timer must snap it back to a real position on its own.
    await act(async () => { vi.advanceTimersByTime(700); });
    expect(trackOffset(track)).toBe('0px');
  });

  it('renders nothing when there are no featured listings', async () => {
    mockFetch.mockResolvedValue([]);
    const { container } = render(<PropertyGallery selectedPropertyId={null} onSelect={() => {}} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(container.querySelector('.gallery-wrapper')).toBeNull();
  });

  it('passes geo to the fetch when a user location is provided', async () => {
    mockFetch.mockResolvedValue(makeListings(2));
    render(
      <PropertyGallery
        selectedPropertyId={null}
        onSelect={() => {}}
        userLocation={{ latitude: -1.29, longitude: 36.82 }}
      />,
    );
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(mockFetch).toHaveBeenCalledWith(undefined, { latitude: -1.29, longitude: 36.82 });
  });
});
