import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { InsarCoverage, ListingRisk } from '../../api/insar';

// The "View Building Risk Analysis" entry must appear ONLY when the listing resolves to a
// single MONITORED building — every other coverage state has no building for the InSAR map
// to focus, so the button would mislead. This test locks the gate across all coverage
// states. PropertyDetails is heavy (Leaflet map, many contexts), so we mock the leaf deps
// and drive only the listing-risk coverage, which is the single input to the gate.

// Per-test coverage holder (null = query still loading / errored).
let mockCoverage: InsarCoverage | null = 'monitored';
const riskFor = (coverage: InsarCoverage | null) =>
  coverage == null
    ? { data: undefined, isLoading: false, isError: true }
    : {
        data: {
          coverage,
          danger_level: coverage === 'monitored' ? 2 : null,
          aoi_code: coverage === 'monitored' ? 'huruma' : null,
          insar_building_id: coverage === 'monitored' ? 42 : null,
          match_method: null,
          match_confidence: null,
        } as ListingRisk,
        isLoading: false,
        isError: false,
      };
vi.mock('../../hooks/useListingRisk', () => ({
  useListingRisk: () => riskFor(mockCoverage),
}));

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ token: 'tok', user: { id: 'u1', roles: ['individual'] } }),
}));
// No reveal cached → falls back to the listing's (fuzzed) coords, which is enough to render
// the location section (and therefore the button) when coords exist.
vi.mock('../../context/RevealContext', () => ({
  useReveal: () => ({ requestReveal: vi.fn(), getRevealed: () => null }),
}));
vi.mock('../../hooks/useFavorites', () => ({
  useFavorites: () => ({ isFavorite: () => false, toggleFavorite: vi.fn() }),
}));
// The Leaflet location map is irrelevant to this gate — stub it out.
vi.mock('../map/PropertyLocationMap', () => ({ default: () => <div data-testid="loc-map" /> }));

const navigate = vi.fn();
vi.mock('react-router-dom', () => ({ useNavigate: () => navigate }));

import { ToastProvider } from '../../context/ToastContext';
import PropertyDetails from './PropertyDetails';
import type { Property } from '../../types/propertyApi';

// Minimal property with coords so the location section (and the gated button) can render.
const property = {
  id: 'L1',
  title: 'Test listing',
  latitude: -1.28,
  longitude: 36.85,
  images: [],
  verification_status: 'verified',
} as unknown as Property;

const LABEL = 'View Building Risk Analysis';

beforeEach(() => { mockCoverage = 'monitored'; });
afterEach(() => { vi.clearAllMocks(); });

function renderDetails() {
  return render(
    <ToastProvider>
      <PropertyDetails property={property} onClose={() => {}} />
    </ToastProvider>,
  );
}

describe('PropertyDetails — risk-map button coverage gate', () => {
  it('shows the button (with the new label) when the listing is monitored', () => {
    mockCoverage = 'monitored';
    renderDetails();
    expect(screen.getByText(LABEL)).toBeTruthy();
  });

  it.each<InsarCoverage>([
    'not_monitored',
    'needs_confirmation',
    'monitored_land',
    'unavailable',
  ])('hides the button when coverage is %s', (coverage) => {
    mockCoverage = coverage;
    renderDetails();
    expect(screen.queryByText(LABEL)).toBeNull();
  });

  it('hides the button while the risk query is still resolving / errored', () => {
    mockCoverage = null;
    renderDetails();
    expect(screen.queryByText(LABEL)).toBeNull();
  });

  it('never uses the old "View on risk map" wording', () => {
    mockCoverage = 'monitored';
    renderDetails();
    expect(screen.queryByText(/View on risk map/i)).toBeNull();
  });
});
