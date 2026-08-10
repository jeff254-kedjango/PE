import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ProfileMenu from './ProfileMenu';
import type { User } from '../../types/auth';

const baseUser: User = {
  id: 'u1',
  name: 'Amina Otieno',
  email: 'amina@example.com',
  phone: '+254700000000',
  created_at: '2026-01-01T00:00:00Z',
};

function renderMenu(user: User | null, pendingBadge = 0) {
  return render(
    <MemoryRouter>
      <ProfileMenu user={user} pendingBadge={pendingBadge} />
    </MemoryRouter>,
  );
}

describe('ProfileMenu — navbar account chip', () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it('renders the avatar image when the user has one', () => {
    renderMenu({ ...baseUser, avatar: '/uploads/avatars/amina.png' });
    const img = screen.getByTestId('shop-avatar-img') as HTMLImageElement;
    expect(img.getAttribute('src')).toContain('/uploads/avatars/amina.png');
    // The avatar never navigates on its own — it's a button, not a link.
    expect(screen.getByTestId('navbar-avatar-trigger').tagName).toBe('BUTTON');
  });

  it('falls back to the initials bubble when no avatar is set', () => {
    renderMenu(baseUser);
    const initial = screen.getByTestId('shop-avatar-initial');
    expect(initial.textContent).toBe('A'); // first letter of "Amina"
  });

  it('shows a "My Profile" popup on hover that links to /profile', async () => {
    renderMenu(baseUser);
    // Popup is closed at rest.
    expect(screen.queryByTestId('navbar-profile-menu')).toBeNull();
    // Hover the wrapper opens it (desktop / fine pointer — jsdom has no matchMedia ⇒ not coarse).
    fireEvent.mouseEnter(screen.getByTestId('navbar-avatar-trigger').parentElement!);
    await waitFor(() => expect(screen.getByTestId('navbar-profile-menu')).toBeInTheDocument());
    const link = screen.getByRole('menuitem', { name: 'My Profile' });
    expect(link.getAttribute('href')).toBe('/profile');
  });

  it('closes the popup on Escape', async () => {
    renderMenu(baseUser);
    fireEvent.mouseEnter(screen.getByTestId('navbar-avatar-trigger').parentElement!);
    await waitFor(() => expect(screen.getByTestId('navbar-profile-menu')).toBeInTheDocument());
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByTestId('navbar-profile-menu')).toBeNull());
  });

  it('surfaces the admin pending badge on the avatar', () => {
    renderMenu(baseUser, 3);
    expect(screen.getByLabelText('3 pending applications').textContent).toBe('3');
  });

  // Touch: on a coarse pointer the popup opens on TAP (not hover). isCoarsePointer() is read once at
  // mount, so stub matchMedia BEFORE render (mirrors ShopHoverCard's touch tests).
  describe('coarse pointer (touch)', () => {
    it('opens on tap and stays inert on hover', async () => {
      vi.stubGlobal('matchMedia', (q: string) => ({
        matches: q.includes('coarse'),
        media: q, onchange: null,
        addEventListener: vi.fn(), removeEventListener: vi.fn(),
        addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
      }));
      renderMenu(baseUser);
      // Hover is inert on touch.
      fireEvent.mouseEnter(screen.getByTestId('navbar-avatar-trigger').parentElement!);
      expect(screen.queryByTestId('navbar-profile-menu')).toBeNull();
      // Tapping the avatar opens the popup.
      fireEvent.click(screen.getByTestId('navbar-avatar-trigger'));
      await waitFor(() => expect(screen.getByTestId('navbar-profile-menu')).toBeInTheDocument());
      expect(screen.getByRole('menuitem', { name: 'My Profile' }).getAttribute('href')).toBe('/profile');
    });
  });
});
