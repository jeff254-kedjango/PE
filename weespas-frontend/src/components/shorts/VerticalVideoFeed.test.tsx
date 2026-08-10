import { describe, it, expect, vi, beforeAll, afterEach } from 'vitest';
import { render } from '@testing-library/react';
import VerticalVideoFeed from './VerticalVideoFeed';

// jsdom has no IntersectionObserver; useActiveIndex constructs one on mount.
beforeAll(() => {
  class IO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal('IntersectionObserver', IO);
});

// Mock the data hook so we land on the loading branch — that renders the root
// (with the embedded class + state modifier) and runs the top-level effects,
// without instantiating ShortItem (favorites / IntersectionObserver coupling).
vi.mock('../../hooks/useShortsFeed', () => ({
  useShortsFeed: () => ({
    items: [],
    isLoading: true,
    isError: false,
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
  }),
}));

afterEach(() => {
  vi.restoreAllMocks();
  document.body.style.overflow = '';
});

describe('VerticalVideoFeed embedded mode', () => {
  it('applies the --embedded root class and does not lock body scroll', () => {
    const { container } = render(
      <VerticalVideoFeed embedded token={null} onSelect={() => {}} />,
    );
    expect(container.querySelector('.vertical-video-feed--embedded')).toBeTruthy();
    // The page must keep scrolling — the body lock is full-screen-only.
    expect(document.body.style.overflow).not.toBe('hidden');
  });

  it('locks body scroll in full-screen mode (no embedded prop)', () => {
    render(<VerticalVideoFeed token={null} onSelect={() => {}} />);
    expect(document.body.style.overflow).toBe('hidden');
  });

  it('omits the close button when no onExit is given (embedded)', () => {
    const { container } = render(
      <VerticalVideoFeed embedded token={null} onSelect={() => {}} />,
    );
    expect(container.querySelector('.vertical-video-feed__close')).toBeNull();
  });
});

// Regression guard for the tablet bug: the nav buttons must scroll the TRACK
// only, never bubble to the page. scrollIntoView() walks every scrollable
// ancestor (incl. the window) and dragged the embedded agents page down; the
// fix scrolls the track via scrollBy and must NOT touch scrollIntoView.
describe('VerticalVideoFeed nav scrolling is track-local (no page bubble)', () => {
  it('clicking "Next video" calls track.scrollBy and not scrollIntoView', async () => {
    vi.resetModules();
    // Stub ShortItem so we don't drag in the favorites/Auth context coupling —
    // we only need the nav buttons + track to exist. A spacer-like div with the
    // index attribute is enough for scrollToIndex's querySelector.
    vi.doMock('./ShortItem', () => ({
      default: ({ index }: { index: number }) => (
        <div data-short-index={index} className="short-item-stub" />
      ),
    }));
    // Two non-dismissed items so the nav renders and "next" is enabled.
    vi.doMock('../../hooks/useShortsFeed', () => ({
      useShortsFeed: () => ({
        items: [
          { id: 'a', media_url: '', poster_url: '', property_id: 'p1' },
          { id: 'b', media_url: '', poster_url: '', property_id: 'p2' },
        ],
        isLoading: false,
        isError: false,
        fetchNextPage: vi.fn(),
        hasNextPage: false,
        isFetchingNextPage: false,
      }),
    }));
    const scrollBy = vi.fn();
    const scrollIntoView = vi.fn();
    HTMLElement.prototype.scrollBy = scrollBy as unknown as typeof HTMLElement.prototype.scrollBy;
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    HTMLElement.prototype.getBoundingClientRect = () =>
      ({ top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0, toJSON() {} }) as DOMRect;

    const { default: Feed } = await import('./VerticalVideoFeed');
    const { container } = render(<Feed embedded token={null} onSelect={() => {}} />);

    const nextBtn = container.querySelectorAll<HTMLButtonElement>('.vertical-video-feed__nav-btn')[1];
    expect(nextBtn).toBeTruthy();
    nextBtn.click();

    expect(scrollBy).toHaveBeenCalled();
    expect(scrollIntoView).not.toHaveBeenCalled();
    vi.doUnmock('../../hooks/useShortsFeed');
    vi.doUnmock('./ShortItem');
  });
});
