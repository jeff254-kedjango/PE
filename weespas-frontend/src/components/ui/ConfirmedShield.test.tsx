import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import ConfirmedShield from './ConfirmedShield';

describe('ConfirmedShield', () => {
  it('renders an icon-only green shield with the honest provenance tooltip', () => {
    const { container } = render(<ConfirmedShield />);
    const badge = container.querySelector('.confirmed-shield');
    expect(badge).toBeTruthy();
    // Tooltip must say "assessment", never "safe" — green is provenance, not a verdict.
    expect(badge?.getAttribute('title')).toBe('Confirmed by an on-the-ground assessment');
    expect(badge?.getAttribute('title')?.toLowerCase()).not.toContain('safe');
    // Icon-only: an svg, no text label.
    expect(container.querySelector('svg')).toBeTruthy();
    expect(badge?.textContent?.trim()).toBe('');
  });
});
