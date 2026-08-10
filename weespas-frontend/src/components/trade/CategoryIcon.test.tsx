// Parity + render tests for the trending product card's category glyph. Because the card has no shop
// name, the icon is a primary signal — a category with no glyph would render blank, so we assert
// every legal slug has one and that an unknown slug falls back to the neutral glyph (never empty).
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import CategoryIcon, { hasGlyphForEverySlug } from './CategoryIcon';
import { CATEGORY_SLUGS } from '../../utils/categories';

describe('CategoryIcon', () => {
  it('has a glyph for every legal category slug', () => {
    expect(hasGlyphForEverySlug()).toBe(true);
    expect(CATEGORY_SLUGS.length).toBeGreaterThan(0);
  });

  it('renders an SVG path for each slug', () => {
    for (const slug of CATEGORY_SLUGS) {
      const { container, unmount } = render(<CategoryIcon category={slug} />);
      const svg = container.querySelector('svg');
      expect(svg).not.toBeNull();
      expect(svg!.getAttribute('viewBox')).toBe('0 0 24 24');
      expect(svg!.getAttribute('fill')).toBe('currentColor');
      expect(container.querySelector('path')).not.toBeNull();
      unmount();
    }
  });

  it('falls back to the neutral glyph for an unknown / null slug (never blank)', () => {
    for (const cat of [null, undefined, 'not-a-real-slug']) {
      const { container, unmount } = render(<CategoryIcon category={cat as string | null} />);
      expect(container.querySelector('path')).not.toBeNull();
      unmount();
    }
  });

  it('respects the size prop', () => {
    const { container } = render(<CategoryIcon category="butchery" size={42} />);
    const svg = container.querySelector('svg')!;
    expect(svg.getAttribute('width')).toBe('42');
    expect(svg.getAttribute('height')).toBe('42');
  });
});
