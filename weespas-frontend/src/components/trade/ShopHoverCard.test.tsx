import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../api/commerce', async () => {
  const actual = await vi.importActual<typeof import('../../api/commerce')>('../../api/commerce');
  return { ...actual, getShopProfile: vi.fn(), toggleShopFollow: vi.fn() };
});

import { getShopProfile, toggleShopFollow, type CommerceSession, type ShopProfile } from '../../api/commerce';
import ShopHoverCard from './ShopHoverCard';

const mockGetProfile = vi.mocked(getShopProfile);
const mockToggleFollow = vi.mocked(toggleShopFollow);

const SESSION: CommerceSession = { token: 'tok', commerce_url: 'http://c' };

function profile(over: Partial<ShopProfile> = {}): ShopProfile {
  return {
    shop_id: 'shop1', seller_id: 'sel1', name: 'Mama Njeri Groceries', avatar_url: null, banner_url: null,
    description: 'Fresh produce daily, picked this morning.', contact: '0712 000 000',
    category: null,
    property_uuid: null, follower_count: 4, following: false, rating: 4.5, review_count: 12,
    ...over,
  };
}

// jsdom defaults to pointer:fine (not coarse) → desktop hover path.
function renderCard(onOpenProfile = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={qc}>
      <ShopHoverCard session={SESSION} shopId="shop1" onOpenProfile={onOpenProfile}>
        <span>MN</span>
      </ShopHoverCard>
    </QueryClientProvider>,
  );
  return { ...utils, onOpenProfile };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetProfile.mockResolvedValue(profile());
});

describe('ShopHoverCard', () => {
  it('does not fetch until opened (no N+1 across the feed)', () => {
    renderCard();
    expect(mockGetProfile).not.toHaveBeenCalled();
    expect(screen.queryByTestId('shop-hovercard')).toBeNull();
  });

  it('opens on hover and shows the published card', async () => {
    renderCard();
    fireEvent.mouseEnter(screen.getByTestId('shop-avatar-trigger').parentElement!);
    await waitFor(() => expect(screen.getByTestId('shop-hovercard-name').textContent).toBe('Mama Njeri Groceries'));
    expect(screen.getByTestId('shop-hovercard-contact').textContent).toContain('0712 000 000');
    expect(screen.getByTestId('shop-hovercard-desc').textContent).toContain('Fresh produce');
  });

  it('toggles follow via the Notify button', async () => {
    mockToggleFollow.mockResolvedValue({ shop_id: 'shop1', following: true, follower_count: 5 });
    renderCard();
    fireEvent.mouseEnter(screen.getByTestId('shop-avatar-trigger').parentElement!);
    await waitFor(() => expect(screen.getByTestId('shop-hovercard-notify')).toBeInTheDocument());
    expect(screen.getByTestId('shop-hovercard-notify').textContent).toContain('Notify');
    fireEvent.click(screen.getByTestId('shop-hovercard-notify'));
    await waitFor(() => expect(mockToggleFollow).toHaveBeenCalledWith(SESSION, 'shop1'));
    // The card flips to "Following" from the mutation result (no refetch).
    await waitFor(() => expect(screen.getByTestId('shop-hovercard-notify').textContent).toContain('Following'));
  });

  it('opens the storefront from the "Store Front" button', async () => {
    const { onOpenProfile } = renderCard();
    fireEvent.mouseEnter(screen.getByTestId('shop-avatar-trigger').parentElement!);
    await waitFor(() => expect(screen.getByTestId('shop-hovercard-profile')).toBeInTheDocument());
    // #5a — the label reads "Store Front" (not "Profile").
    expect(screen.getByTestId('shop-hovercard-profile').textContent).toContain('Store Front');
    fireEvent.click(screen.getByTestId('shop-hovercard-profile'));
    expect(onOpenProfile).toHaveBeenCalledOnce();
  });

  // #5b — the cover strip is ALWAYS rendered (avatar sits on top of it), even with no banner
  // uploaded (the gradient fallback), so the avatar always has a backdrop to overlap.
  it('always renders the cover backdrop, even without a banner image', async () => {
    mockGetProfile.mockResolvedValue(profile({ banner_url: null }));
    renderCard();
    fireEvent.mouseEnter(screen.getByTestId('shop-avatar-trigger').parentElement!);
    await waitFor(() => expect(screen.getByTestId('shop-hovercard-banner')).toBeInTheDocument());
    // No <img> when there's no banner, but the cover container (with the avatar) is present.
    expect(screen.getByTestId('shop-hovercard-banner').querySelector('img')).toBeNull();
    expect(screen.getByTestId('shop-avatar-initial')).toBeInTheDocument();
  });

  it('omits the contact and description rows when the shop has none', async () => {
    mockGetProfile.mockResolvedValue(profile({ contact: null, description: null }));
    renderCard();
    fireEvent.mouseEnter(screen.getByTestId('shop-avatar-trigger').parentElement!);
    await waitFor(() => expect(screen.getByTestId('shop-hovercard-name')).toBeInTheDocument());
    expect(screen.queryByTestId('shop-hovercard-contact')).toBeNull();
    expect(screen.queryByTestId('shop-hovercard-desc')).toBeNull();
  });

  it('closes on Escape', async () => {
    renderCard();
    fireEvent.mouseEnter(screen.getByTestId('shop-avatar-trigger').parentElement!);
    await waitFor(() => expect(screen.getByTestId('shop-hovercard')).toBeInTheDocument());
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByTestId('shop-hovercard')).toBeNull());
  });

  // Touch path: on a coarse pointer the card must open on TAP (not hover) and be dismissable —
  // this is the mobile behaviour the desktop-hover tests above never exercise. isCoarsePointer()
  // is read once at mount, so stub matchMedia BEFORE render.
  describe('coarse pointer (touch)', () => {
    beforeEach(() => {
      vi.stubGlobal('matchMedia', (q: string) => ({
        matches: q.includes('coarse'),
        media: q, onchange: null,
        addEventListener: vi.fn(), removeEventListener: vi.fn(),
        addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
      }));
    });
    afterEach(() => { vi.unstubAllGlobals(); });

    it('opens on tap and does NOT open on hover', async () => {
      renderCard();
      // Hover must be inert on touch.
      fireEvent.mouseEnter(screen.getByTestId('shop-avatar-trigger').parentElement!);
      expect(screen.queryByTestId('shop-hovercard')).toBeNull();
      // Tapping the trigger opens it.
      fireEvent.click(screen.getByTestId('shop-avatar-trigger'));
      await waitFor(() => expect(screen.getByTestId('shop-hovercard-name').textContent).toBe('Mama Njeri Groceries'));
    });

    it('dismisses on an outside tap', async () => {
      renderCard();
      fireEvent.click(screen.getByTestId('shop-avatar-trigger'));
      await waitFor(() => expect(screen.getByTestId('shop-hovercard')).toBeInTheDocument());
      fireEvent.pointerDown(document.body);
      await waitFor(() => expect(screen.queryByTestId('shop-hovercard')).toBeNull());
    });
  });
});
