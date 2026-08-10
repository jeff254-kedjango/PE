// Shop categories — the frontend mirror of the backend taxonomy (commerce core/categories.py).
//
// The BACKEND owns the legal slugs and validates them; it returns only the opaque slug on the wire
// (FeedItem.shop_category / ShopProfile.category). The COLOR is a purely-presentational concern
// owned here: each slug maps to a human label (for the seller's category picker) and a CSS color
// token (defined in styles/variables.css) that paints the §8 trending rail's category cards and a
// subtle tint on the feed card. This split means a palette re-tune never touches the API.
//
// A parity test (categories.test.ts) asserts CATEGORY_SLUGS here stays in lock-step with the
// backend list, so the two can't silently drift (an unknown slug would otherwise render as the
// neutral fallback with no error).

export interface CategoryMeta {
  /** Human label for the seller's category picker + the rail card. */
  readonly label: string;
  /** The CSS custom property (defined in variables.css) carrying this category's color. */
  readonly colorVar: string;
}

// slug → { label, colorVar }. Order here drives the seller picker's option order.
export const CATEGORY_META: Record<string, CategoryMeta> = {
  butchery: { label: 'Butchery', colorVar: '--color-cat-butchery' },
  bakery: { label: 'Bakery', colorVar: '--color-cat-bakery' },
  greengrocer: { label: 'Mama Mboga / Greengrocer', colorVar: '--color-cat-greengrocer' },
  restaurant: { label: 'Food & Restaurant', colorVar: '--color-cat-restaurant' },
  boutique: { label: 'Boutique / Exotic / Wedding', colorVar: '--color-cat-boutique' },
  electronics: { label: 'Electronics', colorVar: '--color-cat-electronics' },
  shoes: { label: 'Shoe Store', colorVar: '--color-cat-shoes' },
  beauty: { label: 'Beauty & Salon', colorVar: '--color-cat-beauty' },
  hardware: { label: 'Hardware', colorVar: '--color-cat-hardware' },
  pharmacy: { label: 'Pharmacy', colorVar: '--color-cat-pharmacy' },
  general: { label: 'General / Duka', colorVar: '--color-cat-general' },
};

/** The legal category slugs, in picker order. Kept in parity with the backend SHOP_CATEGORIES. */
export const CATEGORY_SLUGS: readonly string[] = Object.keys(CATEGORY_META);

/** A neutral color token for an un-categorised shop (null/unknown slug) — keeps the rail coherent
 *  without implying a category. Defined in variables.css. */
export const CATEGORY_NEUTRAL_VAR = '--color-cat-neutral';

/** The CSS `var(...)` color expression for a category slug (or the neutral fallback for
 *  null/unknown). Use directly in an inline style, e.g. `style={{ color: categoryColor(c) }}`. */
export function categoryColor(slug: string | null | undefined): string {
  const meta = slug ? CATEGORY_META[slug] : undefined;
  return `var(${meta ? meta.colorVar : CATEGORY_NEUTRAL_VAR})`;
}

/** The display label for a category slug; '' for null/unknown (the caller omits the chip). */
export function categoryLabel(slug: string | null | undefined): string {
  const meta = slug ? CATEGORY_META[slug] : undefined;
  return meta ? meta.label : '';
}
