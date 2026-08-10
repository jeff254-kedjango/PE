import { describe, it, expect } from 'vitest';
import { formatPrice, formatDistance } from './commerce';

describe('formatPrice', () => {
  it('shows whole major units with a thousands separator', () => {
    expect(formatPrice(2000, 'KES')).toBe('KES 20');
    expect(formatPrice(1234500, 'KES')).toBe('KES 12,345');
  });
  it('rounds to the nearest major unit (integer cents in, no float display)', () => {
    expect(formatPrice(2050, 'KES')).toBe('KES 21'); // 20.5 → 21
  });
});

describe('formatDistance', () => {
  it('uses metres under 1 km, rounded to 10 m', () => {
    expect(formatDistance(324)).toBe('320 m away');
    expect(formatDistance(995)).toBe('1000 m away');
  });
  it('uses kilometres at/over 1 km (one decimal) — a far national item is shown honestly', () => {
    expect(formatDistance(1500)).toBe('1.5 km away');
    expect(formatDistance(440000)).toBe('440.0 km away');
  });
});
