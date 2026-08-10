import { describe, it, expect, vi, afterEach } from 'vitest';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AgentsPage from './AgentsPage';

// Mock the data + env dependencies so the test targets the desktop/mobile
// branch logic only.
const mockIsDesktop = vi.fn();
vi.mock('../hooks/useMediaQuery', () => ({ useMediaQuery: () => mockIsDesktop() }));
vi.mock('../hooks/usePublicAgents', () => ({
  usePublicAgents: () => ({ data: { items: [], total: 0 }, isLoading: false, isError: false }),
}));
vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ token: null }) }));
// Stub the feed so we don't need the shorts query / video elements — we only
// assert it is (or isn't) mounted.
vi.mock('../components/shorts/VerticalVideoFeed', () => ({
  default: () => <div className="vertical-video-feed--embedded" data-testid="feed" />,
}));

afterEach(() => { vi.restoreAllMocks(); });

function renderPage() {
  return render(<MemoryRouter><AgentsPage /></MemoryRouter>);
}

describe('AgentsPage responsive layout', () => {
  it('mobile (<768px): no video rail, no two-column layout — structure unchanged', () => {
    mockIsDesktop.mockReturnValue(false);
    const { container } = renderPage();
    expect(container.querySelector('.ag-layout')).toBeNull();
    expect(container.querySelector('.vertical-video-feed--embedded')).toBeNull();
    expect(container.querySelector('.ag-results')).toBeTruthy();
  });

  it('desktop (≥768px): mounts the video rail inside the two-column layout', () => {
    mockIsDesktop.mockReturnValue(true);
    const { container } = renderPage();
    expect(container.querySelector('.ag-layout')).toBeTruthy();
    expect(container.querySelector('.ag-video-col .vertical-video-feed--embedded')).toBeTruthy();
    expect(container.querySelector('.ag-results')).toBeTruthy();
  });
});
