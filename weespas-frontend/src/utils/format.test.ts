import { describe, it, expect } from 'vitest';
import { formatPrice, formatDistance, formatBedBath, formatCompactCount } from './format';

describe('formatPrice', () => {
  it('abbreviates millions', () => {
    expect(formatPrice(8_500_000)).toBe('KES 8.5M');
    expect(formatPrice(5_000_000)).toBe('KES 5M');
  });

  it('abbreviates thousands', () => {
    expect(formatPrice(25_000)).toBe('KES 25K');
    expect(formatPrice(35_500)).toBe('KES 35.5K');
  });

  it('appends /mo for rentals', () => {
    expect(formatPrice(35_000, 'KES', 'rent')).toBe('KES 35K/mo');
  });

  it('handles missing price', () => {
    expect(formatPrice(undefined)).toBe('Price on request');
  });
});

describe('formatDistance', () => {
  it('formats sub-100m, metres, and kilometres', () => {
    expect(formatDistance(0.05)).toBe('< 100 m');
    expect(formatDistance(0.5)).toBe('500 m');
    expect(formatDistance(1.234)).toBe('1.2 km');
  });

  it('returns empty for undefined', () => {
    expect(formatDistance(undefined)).toBe('');
  });
});

describe('formatBedBath', () => {
  it('formats bed + bath', () => {
    expect(formatBedBath(3, 2)).toBe('3 bed · 2 bath');
  });

  it('treats 0 bedrooms as a studio', () => {
    expect(formatBedBath(0, 1)).toBe('Studio · 1 bath');
  });

  it('returns empty when nothing provided', () => {
    expect(formatBedBath(undefined, undefined)).toBe('');
  });
});

describe('formatCompactCount', () => {
  it('shows small counts verbatim', () => {
    expect(formatCompactCount(1)).toBe('1');
    expect(formatCompactCount(12)).toBe('12');
    expect(formatCompactCount(999)).toBe('999');
  });

  it('abbreviates thousands', () => {
    expect(formatCompactCount(1_000)).toBe('1k');
    expect(formatCompactCount(1_500)).toBe('1.5k');
    expect(formatCompactCount(20_000)).toBe('20k');
  });

  it('abbreviates millions', () => {
    expect(formatCompactCount(1_000_000)).toBe('1M');
    expect(formatCompactCount(2_300_000)).toBe('2.3M');
  });

  it('clamps non-positive / nullish / non-finite to "0"', () => {
    expect(formatCompactCount(0)).toBe('0');
    expect(formatCompactCount(-5)).toBe('0');
    expect(formatCompactCount(undefined)).toBe('0');
    expect(formatCompactCount(null)).toBe('0');
    expect(formatCompactCount(Infinity)).toBe('0');
  });
});
