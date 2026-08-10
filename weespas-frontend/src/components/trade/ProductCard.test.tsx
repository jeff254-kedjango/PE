import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock the commerce API surface the card + comment thread call, keeping the pure display helpers
// (formatPrice/formatDistance) and the type exports real.
vi.mock('../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../api/commerce')>('../../api/commerce');
  return {
    ...actual,
    toggleSave: vi.fn(),
    createInquiry: vi.fn(),
    listComments: vi.fn(),
    postComment: vi.fn(),
    toggleCommentLike: vi.fn(),
  };
});

import { toggleSave, createInquiry, listComments, postComment, toggleCommentLike } from '../../api/commerce';
import ProductCard from './ProductCard';
import type { FeedItem, CommerceSession } from '../../api/commerce';

const mockToggleSave = vi.mocked(toggleSave);
const mockCreateInquiry = vi.mocked(createInquiry);
const mockListComments = vi.mocked(listComments);
const mockPostComment = vi.mocked(postComment);
const mockToggleCommentLike = vi.mocked(toggleCommentLike);

const SESSION: CommerceSession = { token: 'tok', commerce_url: 'http://c' };

function makeItem(overrides: Partial<FeedItem> = {}): FeedItem {
  return {
    id: 'l1', shop_id: 's1', seller_id: 'sel1', shop_name: 'Mama Njeri', shop_avatar_url: null, shop_category: null, property_uuid: null,
    title: 'Sukuma 1 bunch', description: null, price_cents: 2000, currency: 'KES', media_urls: [],
    distance_m: 320, score: 0.5, save_count: 0, saved_by_me: false, comment_count: 0, is_short_video: false, post_kind: 'product',
    seller_rating: null, seller_review_count: 0,
    is_promoted: false, is_sponsored: false, boost_tier: null,
    created_at: '2026-06-29T10:00:00Z',
    ...overrides,
  };
}

function renderCard(item: FeedItem, confirmed = false, session: CommerceSession | null = SESSION) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ProductCard item={item} confirmed={confirmed} session={session} onSelect={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockListComments.mockResolvedValue({ items: [], next_cursor: null });
});

describe('ProductCard — honesty contracts (unchanged in the social redesign)', () => {
  it('labels a sponsored item "Boosted" with its tier', () => {
    renderCard(makeItem({ is_sponsored: true, boost_tier: 'sovereign' }));
    const label = screen.getByTestId('boosted-label');
    expect(label.textContent?.toLowerCase()).toContain('boosted');
    expect(label.textContent?.toLowerCase()).toContain('sovereign');
  });

  it('NEVER hides the Boosted label on a sponsored item even without a tier', () => {
    renderCard(makeItem({ is_sponsored: true, boost_tier: null }));
    expect(screen.getByTestId('boosted-label')).toBeTruthy();
  });

  it('shows "Selling now" for a promoted (not sponsored) item', () => {
    renderCard(makeItem({ is_promoted: true }));
    expect(screen.getByTestId('selling-now')).toBeTruthy();
  });

  it('does not show "Selling now" once an item is sponsored (sponsored label wins)', () => {
    renderCard(makeItem({ is_promoted: true, is_sponsored: true }));
    expect(screen.queryByTestId('selling-now')).toBeNull();
    expect(screen.getByTestId('boosted-label')).toBeTruthy();
  });

  it('renders the Confirmed shield only when confirmed', () => {
    const { container, rerender } = renderCard(makeItem(), false);
    expect(container.querySelector('.confirmed-shield')).toBeNull();
    const qc = new QueryClient();
    rerender(
      <QueryClientProvider client={qc}>
        <ProductCard item={makeItem()} confirmed session={SESSION} onSelect={() => {}} />
      </QueryClientProvider>,
    );
    expect(container.querySelector('.confirmed-shield')).toBeTruthy();
  });

  it('shows price, distance and rating', () => {
    renderCard(makeItem({ seller_rating: 4.5, seller_review_count: 12 }));
    expect(screen.getByText('KES 20')).toBeTruthy();
    expect(screen.getByText('320 m away')).toBeTruthy();
    expect(screen.getByText('4.5')).toBeTruthy();
  });
});

describe('ProductCard — §8 post kind', () => {
  it('shows the Video badge only for a declared short-video post (with media)', () => {
    // The badge lives in the media wrap, and the Listings card drops video slides, so the post
    // needs an IMAGE for the wrap (and thus the badge) to render.
    const media = ['/uploads/trade/images/c.png', '/uploads/trade/videos/c.mp4'];
    const { rerender, container } = renderCard(makeItem({ is_short_video: false, media_urls: media }));
    expect(screen.queryByTestId('video-badge')).toBeNull();
    const qc = new QueryClient();
    rerender(
      <QueryClientProvider client={qc}>
        <ProductCard item={makeItem({ is_short_video: true, media_urls: media })} confirmed={false} session={SESSION} onSelect={() => {}} />
      </QueryClientProvider>,
    );
    expect(container.querySelector('[data-testid="video-badge"]')).toBeTruthy();
  });
});

describe('ProductCard — media carousel', () => {
  it('renders a carousel with arrows for multiple media', () => {
    renderCard(makeItem({ media_urls: ['/uploads/trade/images/a.png', '/uploads/trade/images/b.png'] }));
    expect(screen.getByTestId('media-carousel')).toBeInTheDocument();
    expect(screen.getByTestId('carousel-next')).toBeInTheDocument();
  });

  it('renders a carousel (no arrows) for a single image', () => {
    renderCard(makeItem({ media_urls: ['/uploads/trade/images/a.png'] }));
    expect(screen.getByTestId('media-carousel')).toBeInTheDocument();
    expect(screen.queryByTestId('carousel-next')).toBeNull();
  });

  it('shows a fallback initial (no carousel) for a product with no media', () => {
    renderCard(makeItem({ media_urls: [] }));
    expect(screen.queryByTestId('media-carousel')).toBeNull();
  });

  it('drops video slides — a mixed image+video post shows images only (no <video>)', () => {
    const { container } = renderCard(makeItem({
      media_urls: ['/uploads/trade/images/a.png', '/uploads/trade/videos/b.mp4', '/uploads/trade/images/c.png'],
    }));
    // Two images survive → carousel with arrows; the video slide is filtered out entirely.
    expect(screen.getByTestId('media-carousel')).toBeInTheDocument();
    expect(screen.getByTestId('carousel-next')).toBeInTheDocument();
    expect(container.querySelector('video')).toBeNull();
  });

  it('renders the fallback (no carousel) for a video-ONLY product — nothing to show in Listings', () => {
    renderCard(makeItem({ media_urls: ['/uploads/trade/videos/only.mp4'] }));
    expect(screen.queryByTestId('media-carousel')).toBeNull();
  });
});

describe('ProductCard — engagement bar', () => {
  it('opens the storefront when the seller header is tapped', () => {
    const onSelect = vi.fn();
    const qc = new QueryClient();
    render(
      <QueryClientProvider client={qc}>
        <ProductCard item={makeItem()} confirmed={false} session={SESSION} onSelect={onSelect} />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByTestId('open-storefront'));
    expect(onSelect).toHaveBeenCalledOnce();
  });

  it('toggles save and reflects the server count', async () => {
    mockToggleSave.mockResolvedValue({ listing_id: 'l1', saved: true, save_count: 1 });
    renderCard(makeItem({ save_count: 0 }));
    fireEvent.click(screen.getByTestId('save-btn'));
    await waitFor(() => expect(mockToggleSave).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.getByTestId('save-btn').textContent).toContain('1'));
  });

  it('seeds the heart from saved_by_me so a prior save shows on mount', () => {
    // Regression: the card used to default saved=false, so a listing the buyer had already saved
    // rendered as un-saved until they re-toggled. It now seeds from the feed item's saved_by_me.
    renderCard(makeItem({ saved_by_me: true, save_count: 4 }));
    expect(screen.getByTestId('save-btn').getAttribute('aria-pressed')).toBe('true');
    // and an un-saved item stays un-pressed
    renderCard(makeItem({ id: 'l2', saved_by_me: false }));
    expect(screen.getAllByTestId('save-btn')[1].getAttribute('aria-pressed')).toBe('false');
  });

  it('sends the "is this available?" inquiry on Ask and locks the button', async () => {
    mockCreateInquiry.mockResolvedValue({
      id: 'i1', listing_id: 'l1', listing_title: 'Sukuma 1 bunch',
      message: 'Is this still available?', created_at: '2026-06-29T10:00:00Z',
    });
    renderCard(makeItem());
    fireEvent.click(screen.getByTestId('ask-btn'));
    await waitFor(() => expect(mockCreateInquiry).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.getByTestId('ask-btn').textContent).toContain('Asked'));
  });

  it('opens the comment thread on demand (no fetch until opened — avoids N+1)', async () => {
    renderCard(makeItem({ comment_count: 2 }));
    // Thread not mounted, so no comments fetched yet.
    expect(screen.queryByTestId('comment-thread')).toBeNull();
    expect(mockListComments).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('comments-btn'));
    expect(screen.getByTestId('comment-thread')).toBeTruthy();
    await waitFor(() => expect(mockListComments).toHaveBeenCalled());
  });

  it('shows the commenter name (and a neutral fallback for older nameless comments) — never the raw id', async () => {
    mockListComments.mockResolvedValue({
      items: [
        { id: 'c1', listing_id: 'l1', author_uuid: '8f3a9c12-dead-beef-0000-111122223333', author_name: 'Asha Kimani', body: 'Still available?', like_count: 0, liked_by_me: false, created_at: '2026-06-29T10:00:00Z' },
        { id: 'c2', listing_id: 'l1', author_uuid: '7b2c4e90-cafe-0000-1111-222233334444', author_name: null, body: 'Price?', like_count: 0, liked_by_me: false, created_at: '2026-06-29T09:00:00Z' },
      ],
      next_cursor: null,
    });
    renderCard(makeItem({ comment_count: 2 }));
    fireEvent.click(screen.getByTestId('comments-btn'));
    await waitFor(() => expect(screen.getByText('Asha Kimani')).toBeInTheDocument());
    // Nameless (older) comment falls back to the neutral label.
    expect(screen.getByText('Weespas user')).toBeInTheDocument();
    // The raw uuid (or any 8-char slice of it) must NOT appear.
    expect(screen.queryByText(/8f3a9c12/)).toBeNull();
    expect(screen.queryByText(/7b2c4e90/)).toBeNull();
  });

  it('posts a comment through the thread composer', async () => {
    mockPostComment.mockResolvedValue({
      id: 'c1', listing_id: 'l1', author_uuid: 'buyer-1', author_name: 'Asha', body: 'Hi', like_count: 0, liked_by_me: false, created_at: '2026-06-29T10:00:00Z',
    });
    renderCard(makeItem());
    fireEvent.click(screen.getByTestId('comments-btn'));
    const input = screen.getByLabelText('Add a public comment') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Hi' } });
    fireEvent.click(screen.getByText('Post'));
    await waitFor(() => expect(mockPostComment).toHaveBeenCalledWith(SESSION, 'l1', 'Hi'));
  });

  it('disables engagement actions without a session (read-only)', () => {
    renderCard(makeItem({ save_count: 3 }), false, null);
    expect((screen.getByTestId('save-btn') as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId('ask-btn') as HTMLButtonElement).disabled).toBe(true);
    // count still displays (read-only social proof)
    expect(screen.getByTestId('save-btn').textContent).toContain('3');
  });
});

describe('ProductCard — product description (preview + read more)', () => {
  const LONG = 'A'.repeat(120) + ' ' + 'B'.repeat(120); // 241 chars, > 150 preview

  it('renders no description block when the listing has none', () => {
    renderCard(makeItem({ description: null }));
    expect(screen.queryByTestId('product-desc')).toBeNull();
  });

  it('shows a short description in full, with no read-more affordance', () => {
    renderCard(makeItem({ description: 'Fresh from the farm this morning.' }));
    expect(screen.getByTestId('product-desc')).toBeInTheDocument();
    expect(screen.getByText('Fresh from the farm this morning.')).toBeInTheDocument();
    expect(screen.queryByTestId('read-more')).toBeNull();
  });

  it('truncates a long description and expands on "read more" / collapses on "show less"', () => {
    renderCard(makeItem({ description: LONG }));
    const desc = screen.getByTestId('product-desc');
    // Collapsed: shorter than the full text, read-more present, show-less absent.
    expect(desc.textContent!.length).toBeLessThan(LONG.length);
    const more = screen.getByTestId('read-more');
    expect(more).toBeInTheDocument();
    expect(screen.queryByTestId('read-less')).toBeNull();
    // Expand → full text visible, show-less now offered.
    fireEvent.click(more);
    expect(screen.getByTestId('read-less')).toBeInTheDocument();
    expect(screen.queryByTestId('read-more')).toBeNull();
    expect(screen.getByTestId('product-desc').textContent).toContain('B'.repeat(120));
    // Collapse again.
    fireEvent.click(screen.getByTestId('read-less'));
    expect(screen.getByTestId('read-more')).toBeInTheDocument();
  });

  it('preserves paragraphs when expanded (blank line → separate <p>)', () => {
    const para = 'First paragraph here.\n\nSecond paragraph here.';
    renderCard(makeItem({ description: para + ' ' + 'x'.repeat(160) })); // long enough to truncate
    fireEvent.click(screen.getByTestId('read-more'));
    const paras = screen.getByTestId('product-desc').querySelectorAll('.product-card__desc-p');
    expect(paras.length).toBeGreaterThanOrEqual(2);
    expect(paras[0].textContent).toContain('First paragraph here.');
    expect(paras[1].textContent).toContain('Second paragraph here.');
  });
});

describe('ProductCard — comment likes', () => {
  it('toggles a like on a comment and reflects the new state', async () => {
    mockListComments.mockResolvedValue({
      items: [
        { id: 'c1', listing_id: 'l1', author_uuid: 'u1', author_name: 'Asha', body: 'Nice', like_count: 0, liked_by_me: false, created_at: '2026-06-29T10:00:00Z' },
      ],
      next_cursor: null,
    });
    mockToggleCommentLike.mockResolvedValue({ comment_id: 'c1', liked: true, like_count: 1 });
    renderCard(makeItem({ comment_count: 1 }));
    fireEvent.click(screen.getByTestId('comments-btn'));
    await waitFor(() => expect(screen.getByTestId('comment-like')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('comment-like'));
    await waitFor(() => expect(mockToggleCommentLike).toHaveBeenCalledWith(SESSION, 'c1'));
  });

  it('shows the like count and a filled heart when the viewer has liked it', async () => {
    mockListComments.mockResolvedValue({
      items: [
        { id: 'c1', listing_id: 'l1', author_uuid: 'u1', author_name: 'Asha', body: 'Nice', like_count: 3, liked_by_me: true, created_at: '2026-06-29T10:00:00Z' },
      ],
      next_cursor: null,
    });
    renderCard(makeItem({ comment_count: 1 }));
    fireEvent.click(screen.getByTestId('comments-btn'));
    await waitFor(() => expect(screen.getByTestId('comment-like')).toBeInTheDocument());
    const like = screen.getByTestId('comment-like');
    expect(like.textContent).toContain('3');
    expect(like.getAttribute('aria-pressed')).toBe('true');
  });

});

describe('ProductCard — plain post rendering', () => {
  const postItem = (over: Partial<FeedItem> = {}) =>
    makeItem({ post_kind: 'post', price_cents: 0, title: 'A neighbourhood update', description: 'Good morning!\n\nSecond para.', ...over });

  it('renders the post body in full (no price, no read-more, no Ask)', () => {
    renderCard(postItem({ media_urls: [] }));
    expect(screen.getByTestId('post-body')).toBeInTheDocument();
    // No price chrome, no read-more truncation, no Ask action on a plain post.
    expect(screen.queryByTestId('read-more')).toBeNull();
    expect(screen.queryByTestId('ask-btn')).toBeNull();
    // Save + Comment still available.
    expect(screen.getByTestId('save-btn')).toBeInTheDocument();
    expect(screen.getByTestId('comments-btn')).toBeInTheDocument();
  });

  it('preserves paragraphs in the post body', () => {
    renderCard(postItem());
    const paras = screen.getByTestId('post-body').querySelectorAll('.product-card__desc-p');
    expect(paras.length).toBeGreaterThanOrEqual(2);
  });

  it('omits the media block for a text-only post', () => {
    const { container } = renderCard(postItem({ media_urls: [] }));
    expect(container.querySelector('.product-card__media')).toBeNull();
  });

  it('a product still shows price + Ask (post branch does not bleed into products)', () => {
    renderCard(makeItem({ post_kind: 'product', price_cents: 2000 }));
    expect(screen.queryByTestId('post-body')).toBeNull();
    expect(screen.getByTestId('ask-btn')).toBeInTheDocument();
  });
});
