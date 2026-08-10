import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import BuildingConfirmMap from './BuildingConfirmMap';
import type { InsarCandidate } from '../../api/insar';

// Leaflet touches the real DOM/canvas which jsdom can't drive; we only care about the
// React-side behaviour (the tap list + confirm button), so stub L with chainable no-ops.
// The 2.5D prism renderer also calls the projection helpers (latLngToLayerPoint /
// layerPointToLatLng / point) and builds polygons/feature-groups — all stubbed here so
// the effects run without throwing; the assertions stay DOM-only.
vi.mock('leaflet', () => {
  const layer = () => ({
    addTo: vi.fn().mockReturnThis(),
    bindTooltip: vi.fn().mockReturnThis(),
    on: vi.fn().mockReturnThis(),
    setStyle: vi.fn().mockReturnThis(),
    remove: vi.fn(),
    getBounds: () => ({ isValid: () => false }),
  });
  const map = {
    remove: vi.fn(),
    fitBounds: vi.fn(),
    on: vi.fn(),
    off: vi.fn(),
    latLngToLayerPoint: vi.fn(({ lat, lng }: { lat: number; lng: number }) => ({ x: lng, y: lat })),
    layerPointToLatLng: vi.fn(({ x, y }: { x: number; y: number }) => ({ lat: y, lng: x })),
  };
  const L = {
    map: vi.fn(() => map),
    tileLayer: vi.fn(() => ({ addTo: vi.fn().mockReturnThis() })),
    geoJSON: vi.fn(() => layer()),
    polygon: vi.fn(() => layer()),
    featureGroup: vi.fn(() => layer()),
    latLng: vi.fn((lat: number, lng: number) => ({ lat, lng })),
    latLngBounds: vi.fn(() => ({ isValid: () => false })),
    point: vi.fn((x: number, y: number) => ({ x, y })),
  };
  return { default: L };
});

const poly: GeoJSON.Geometry = {
  type: 'Polygon',
  coordinates: [[[36.8, -1.27], [36.801, -1.27], [36.801, -1.271], [36.8, -1.27]]],
};

const cand = (over: Partial<InsarCandidate>): InsarCandidate => ({
  insar_building_id: 1,
  aoi_code: 'huruma',
  distance_m: 5,
  height_m: 12,
  n_floors: 4,
  danger_level: 0,
  geometry: poly,
  ...over,
});

describe('BuildingConfirmMap — tap-to-confirm picker', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('lists one tappable option per drawable candidate', () => {
    const { container } = render(
      <BuildingConfirmMap
        candidates={[cand({ insar_building_id: 1 }), cand({ insar_building_id: 2, danger_level: 4 })]}
        onConfirm={vi.fn()}
      />,
    );
    expect(container.querySelectorAll('.bcm__option').length).toBe(2);
  });

  it('skips candidates without a footprint geometry (cannot be tapped)', () => {
    const { container, getByText } = render(
      <BuildingConfirmMap
        candidates={[cand({ insar_building_id: 1, geometry: null })]}
        onConfirm={vi.fn()}
      />,
    );
    // No drawable footprints → honest empty state, never a silent "pick" of nothing.
    expect(getByText(/No nearby buildings/)).toBeTruthy();
    expect(container.querySelector('.bcm__option')).toBeNull();
  });

  it('confirm is disabled until a building is tapped, then fires onConfirm with that id', () => {
    const onConfirm = vi.fn();
    const { container, getByRole } = render(
      <BuildingConfirmMap
        candidates={[cand({ insar_building_id: 7 }), cand({ insar_building_id: 9 })]}
        onConfirm={onConfirm}
      />,
    );
    const confirmBtn = getByRole('button', { name: /Confirm this building/ }) as HTMLButtonElement;
    expect(confirmBtn.disabled).toBe(true);

    const options = container.querySelectorAll('.bcm__option');
    fireEvent.click(options[1]); // pick the second candidate (id 9)
    expect(confirmBtn.disabled).toBe(false);

    fireEvent.click(confirmBtn);
    expect(onConfirm).toHaveBeenCalledWith(9);
  });

  it('marks the tapped option as selected (aria-pressed)', () => {
    const { container } = render(
      <BuildingConfirmMap
        candidates={[cand({ insar_building_id: 1 }), cand({ insar_building_id: 2 })]}
        onConfirm={vi.fn()}
      />,
    );
    const options = container.querySelectorAll('.bcm__option');
    fireEvent.click(options[0]);
    expect(options[0].getAttribute('aria-pressed')).toBe('true');
    expect(options[1].getAttribute('aria-pressed')).toBe('false');
  });

  it('shows "Confirming…" and disables the button while a confirm is in flight', () => {
    const { getByRole, container } = render(
      <BuildingConfirmMap
        candidates={[cand({ insar_building_id: 1 })]}
        onConfirm={vi.fn()}
        confirming
      />,
    );
    fireEvent.click(container.querySelector('.bcm__option')!);
    const btn = getByRole('button', { name: /Confirming/ }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});
