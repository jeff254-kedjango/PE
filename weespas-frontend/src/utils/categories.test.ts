import { describe, it, expect } from 'vitest';
import { CATEGORY_SLUGS, CATEGORY_META, categoryColor, categoryLabel } from './categories';

// NOTE: the cross-language PARITY guard (this list == the backend SHOP_CATEGORIES) lives in the
// commerce pytest suite (tests/test_categories.py), which reads BOTH this file and
// core/categories.py and asserts set-equality — that side can read files without pulling Node
// types into this frontend tsconfig. Here we test the frontend contract + helpers in isolation.

describe('shop categories — frontend contract', () => {
  it('every slug has a label and a category color token', () => {
    expect(CATEGORY_SLUGS.length).toBeGreaterThan(0);
    for (const slug of CATEGORY_SLUGS) {
      expect(CATEGORY_META[slug].label.length).toBeGreaterThan(0);
      expect(CATEGORY_META[slug].colorVar).toMatch(/^--color-cat-/);
    }
  });

  it('includes the documented core categories', () => {
    // A sanity floor so a botched edit that empties the map is caught here, not just in the
    // backend parity test.
    for (const slug of ['butchery', 'bakery', 'greengrocer', 'electronics', 'general']) {
      expect(CATEGORY_SLUGS).toContain(slug);
    }
  });
});

describe('category helpers', () => {
  it('categoryColor maps a known slug to its token and falls back to neutral', () => {
    expect(categoryColor('butchery')).toBe('var(--color-cat-butchery)');
    expect(categoryColor(null)).toBe('var(--color-cat-neutral)');
    expect(categoryColor('not-a-real-slug')).toBe('var(--color-cat-neutral)');
  });

  it('categoryLabel returns the label for a known slug and empty for unknown/null', () => {
    expect(categoryLabel('bakery')).toBe('Bakery');
    expect(categoryLabel(null)).toBe('');
    expect(categoryLabel('nope')).toBe('');
  });
});
