import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import RiskPill from './RiskPill';
import type { ListingRisk } from '../../api/insar';

const risk = (over: Partial<ListingRisk>): ListingRisk => ({
  coverage: 'monitored',
  danger_level: 0,
  aoi_code: 'huruma',
  insar_building_id: 100000,
  match_method: 'pip',
  match_confidence: 1,
  ...over,
});

describe('RiskPill — honest 3-state coverage', () => {
  it('renders the tier label when monitored', () => {
    const { container, getByText } = render(<RiskPill risk={risk({ danger_level: 3 })} />);
    expect(getByText('High')).toBeTruthy();
    expect(container.querySelector('.risk-pill--high')).toBeTruthy();
  });

  it('shows STABLE in a green-toned pill (only a monitored-stable reading is green)', () => {
    const { container } = render(<RiskPill risk={risk({ danger_level: 0 })} />);
    expect(container.querySelector('.risk-pill--stable')).toBeTruthy();
  });

  it('NEVER paints not_monitored as green/safe', () => {
    const { container, getByText } = render(
      <RiskPill risk={risk({ coverage: 'not_monitored', danger_level: null })} />,
    );
    expect(getByText('Not monitored')).toBeTruthy();
    expect(container.querySelector('.risk-pill--unmonitored')).toBeTruthy();
    // Guard the cardinal rule: no tier/green class leaks onto an unmonitored pill.
    expect(container.querySelector('.risk-pill--stable')).toBeNull();
    expect(container.querySelector('.risk-pill--monitored')).toBeNull();
  });

  it('reports unavailable (not safe) when coverage is unavailable', () => {
    const { container, getByText } = render(
      <RiskPill risk={risk({ coverage: 'unavailable', danger_level: null })} />,
    );
    expect(getByText('Risk data unavailable')).toBeTruthy();
    expect(container.querySelector('.risk-pill--unavailable')).toBeTruthy();
  });

  it('treats a fetch error like unavailable — never a silent omission', () => {
    const { container } = render(<RiskPill risk={undefined} isError />);
    expect(container.querySelector('.risk-pill--unavailable')).toBeTruthy();
  });

  it('marks a nearest-match reading as approximate', () => {
    const { getByText } = render(
      <RiskPill risk={risk({ danger_level: 2, match_method: 'nearest', match_confidence: 0.6 })} />,
    );
    expect(getByText(/approx/)).toBeTruthy();
  });

  it('shows a loading placeholder while fetching', () => {
    const { container } = render(<RiskPill risk={undefined} isLoading />);
    expect(container.querySelector('.risk-pill--loading')).toBeTruthy();
  });

  it('shows a distinct "Verifying…" state while pending (not green, not unavailable)', () => {
    const { container, getByText } = render(
      <RiskPill risk={undefined} isPending />,
    );
    expect(getByText(/Verifying/)).toBeTruthy();
    expect(container.querySelector('.risk-pill--pending')).toBeTruthy();
    // pending must not borrow the monitored/stable (green) or unavailable look.
    expect(container.querySelector('.risk-pill--stable')).toBeNull();
    expect(container.querySelector('.risk-pill--unavailable')).toBeNull();
  });

  it('pending takes precedence over a resolved risk payload', () => {
    // Even if a stale monitored reading is present, a pending listing reads "Verifying…".
    const { container } = render(
      <RiskPill risk={risk({ danger_level: 0 })} isPending />,
    );
    expect(container.querySelector('.risk-pill--pending')).toBeTruthy();
    expect(container.querySelector('.risk-pill--stable')).toBeNull();
  });
});

describe('RiskPill — needs_confirmation (provisional worst-case)', () => {
  it('shows the WORST candidate tier with a dashed provisional border', () => {
    const { container, getByText } = render(
      <RiskPill risk={risk({ coverage: 'needs_confirmation', danger_level: 4, candidate_count: 3, provisional: true })} />,
    );
    // Worst tier label is surfaced (Critical), flagged "(nearby)" + "confirm".
    expect(getByText(/Critical \(nearby\)/)).toBeTruthy();
    expect(getByText(/confirm/)).toBeTruthy();
    expect(container.querySelector('.risk-pill--provisional')).toBeTruthy();
    // It borrows the worst tier's colour so it never reads safer than reality.
    expect(container.querySelector('.risk-pill--critical')).toBeTruthy();
  });

  it('never reads as a confirmed monitored/stable pill', () => {
    const { container } = render(
      <RiskPill risk={risk({ coverage: 'needs_confirmation', danger_level: 0, provisional: true })} />,
    );
    // Even a stable worst-case keeps the provisional (dashed) treatment — it is NOT a
    // settled monitored reading until the owner confirms.
    expect(container.querySelector('.risk-pill--provisional')).toBeTruthy();
    expect(container.querySelector('.risk-pill--monitored')).toBeNull();
  });
});

describe('RiskPill — monitored_land (neighbour ground estimate)', () => {
  it('renders a distinct land pill, never a per-building tier', () => {
    const { container, getByText } = render(
      <RiskPill risk={risk({ coverage: 'monitored_land', danger_level: null })} />,
    );
    expect(getByText('Ground estimate (land)')).toBeTruthy();
    expect(container.querySelector('.risk-pill--land')).toBeTruthy();
    // No building tier / green-safe class leaks onto a land estimate.
    expect(container.querySelector('.risk-pill--stable')).toBeNull();
    expect(container.querySelector('.risk-pill--monitored')).toBeNull();
  });
});
