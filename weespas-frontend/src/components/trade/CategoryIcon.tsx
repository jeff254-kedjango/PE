// CategoryIcon — one inline-SVG glyph per trade category, for the §8 trending product card.
//
// The trending card has no shop name/avatar — the category color + this icon ARE the at-a-glance
// "what kind of thing is this" signal (the lunchtime butchery 🥩, the bakery loaf, …). Kept here,
// colocated with the category taxonomy (utils/categories.ts), rather than bloating the shared
// ui/Icon.tsx — these glyphs are trade-specific and used only by the rail.
//
// Every glyph uses fill="currentColor" + one 24×24 viewBox so the caller drives the color (the
// category color) via `color`/`style`. A parity test (CategoryIcon.test.tsx) asserts every legal
// CATEGORY_SLUG has a glyph + the neutral fallback resolves, so a new category can't silently render
// blank.
import React from 'react';
import { CATEGORY_SLUGS } from '../../utils/categories';

// slug → glyph paths. Simple, recognisable silhouettes (fill-based, currentColor).
const GLYPHS: Record<string, React.ReactNode> = {
  // Butchery — a cleaver.
  butchery: (
    <path d="M3 3h2l3 7 8-8 2 2-8 8 1 2H3a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zm-1 16h18v2H2v-2z" />
  ),
  // Bakery — a loaf of bread.
  bakery: (
    <path d="M5 8a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4c1.1 0 2 .9 2 2v1a2 2 0 0 1-2 2v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-5a2 2 0 0 1-2-2v-1c0-1.1.9-2 2-2h2z" />
  ),
  // Greengrocer (mama mboga) — a leaf.
  greengrocer: (
    <path d="M5 21c-1-7 3-15 16-16 1 10-5 16-12 16-1.5 0-2.6-.3-3.4-.7C6 17 8 14 12 12c-4 1-6 4-7 9z" />
  ),
  // Food & restaurant — fork & knife.
  restaurant: (
    <path d="M7 2v8a2 2 0 0 1-2 2v10H3V12a2 2 0 0 1-2-2V2h2v6h1V2h2v6h1V2h0zm10 0c-2 0-3 2-3 5s1 5 3 5v10h2V2h-2z" />
  ),
  // Boutique / wedding — a dress.
  boutique: (
    <path d="M9 2h6l-1 4 3 4-3 2 2 10H7l2-10-3-2 3-4-1-4zm3 6 1.5-2h-3L12 8z" />
  ),
  // Electronics — a microchip.
  electronics: (
    <path d="M8 2v2H6a2 2 0 0 0-2 2v2H2v2h2v4H2v2h2v2a2 2 0 0 0 2 2h2v2h2v-2h4v2h2v-2h2a2 2 0 0 0 2-2v-2h2v-2h-2v-4h2V8h-2V6a2 2 0 0 0-2-2h-2V2h-2v2h-4V2H8zm1 6h6v8H9V8z" />
  ),
  // Shoe store — a shoe.
  shoes: (
    <path d="M2 7l4-1 3 4 6 2c3 .8 7 1.4 7 4.5V18a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V7zm2 8h16v-1c0-1.3-2-1.8-4-2.3l-1 1.3-2-2-1 1.5-2-2L9 13 4 11v4z" />
  ),
  // Beauty & salon — lipstick.
  beauty: (
    <path d="M9 2l4 1 1 5h-5l1-5-1-1zm-1 7h6v3H8V9zm0 4h6v8a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1v-8z" />
  ),
  // Hardware — a wrench.
  hardware: (
    <path d="M21 5a5 5 0 0 1-6.5 6.4l-7 7a2.1 2.1 0 0 1-3-3l7-7A5 5 0 0 1 18 2l-3 3 1.5 2.5L19 9l2-4z" />
  ),
  // Pharmacy — a medical cross.
  pharmacy: (
    <path d="M9 2h6v5h5v6h-5v9H9v-9H4V7h5V2z" />
  ),
  // General / duka — a shopping bag.
  general: (
    <path d="M7 7V6a5 5 0 0 1 10 0v1h3l1 14H3L4 7h3zm2 0h6V6a3 3 0 0 0-6 0v1z" />
  ),
};

interface CategoryIconProps {
  /** Category slug; null/unknown → the neutral fallback (a generic tag). */
  category: string | null | undefined;
  size?: number;
  className?: string;
}

// Neutral fallback for an un-categorised / unknown slug — a price/tag glyph (never blank).
const NEUTRAL_GLYPH = (
  <path d="M21 11l-9-9H4a2 2 0 0 0-2 2v8l9 9 10-10zM6.5 8A1.5 1.5 0 1 1 8 6.5 1.5 1.5 0 0 1 6.5 8z" />
);

const CategoryIcon: React.FC<CategoryIconProps> = ({ category, size = 18, className }) => {
  const glyph = (category && GLYPHS[category]) || NEUTRAL_GLYPH;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      {glyph}
    </svg>
  );
};

/** True iff a glyph exists for every legal category slug (used by the parity test). */
export function hasGlyphForEverySlug(): boolean {
  return CATEGORY_SLUGS.every((slug) => slug in GLYPHS);
}

export default CategoryIcon;
