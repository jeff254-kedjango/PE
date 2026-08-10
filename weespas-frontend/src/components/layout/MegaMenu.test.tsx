import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// openInsarRiskMap is the Risk Map action; mock it so we can assert it fires from the menu.
vi.mock('../../api/insar', () => ({ openInsarRiskMap: vi.fn() }));
import { openInsarRiskMap } from '../../api/insar';
import MegaMenu from './MegaMenu';

const mockOpenInsar = vi.mocked(openInsarRiskMap);

function renderMenu(props: Partial<React.ComponentProps<typeof MegaMenu>> = {}) {
  return render(
    <MemoryRouter>
      <MegaMenu open onClose={() => {}} token="tok" isAuthenticated {...props} />
    </MemoryRouter>,
  );
}

describe('MegaMenu — Trade + Risk Map relocation', () => {
  it('shows InSAR and (authed) Trade alongside the existing services', () => {
    renderMenu();
    expect(screen.getByText('InSAR')).toBeTruthy();
    expect(screen.getByText('Trade')).toBeTruthy();
    // existing services still present (consistency — we ADDED, didn't replace)
    expect(screen.getByText('Customer Care')).toBeTruthy();
    expect(screen.getByText('Agents')).toBeTruthy();
  });

  it('hides Trade for an anonymous user (commerce needs a signed-in user)', () => {
    renderMenu({ isAuthenticated: false });
    expect(screen.queryByText('Trade')).toBeNull();
    // InSAR stays — it's free/login-required and handles the anon path itself.
    expect(screen.getByText('InSAR')).toBeTruthy();
  });

  it('fires the InSAR deep-link on click', () => {
    renderMenu();
    fireEvent.click(screen.getByText('InSAR'));
    expect(mockOpenInsar).toHaveBeenCalledOnce();
  });
});
