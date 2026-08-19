// marketFormat.ts — pure display helpers shared by the WeesStock market surfaces
// (MarketsPage, MarketDetailPage, and the seller card's momentum chip).
//
// Kept in one place because the money/trend conventions MUST be identical everywhere the
// market is shown — a row that formats "KSh 1,097,070" and a detail page that shows
// "KES 1.1M" for the same number would read as two different facts.

/** Trend deltas below this read as noise, not movement — a 3% swing over a 30-day window is
 *  ordinary week-to-week variation and must not be drawn as an arrow. Shared with the seller
 *  card so the market and the card can never disagree about what "steady" means. */
export const TREND_FLAT_BAND = 0.05;

/** Cents → "KSh 12,300", grouped and without decimals. Kenyan retail prices are quoted in
 *  whole shillings; cents exist in the ledger for exactness, not for display. */
export function marketMoney(cents: number, currency: string): string {
  const major = Math.round(cents / 100);
  return `${currency === 'KES' ? 'KSh' : currency} ${major.toLocaleString('en-KE')}`;
}

/** 0..1 → "62%". Whole percent — the market reads coarse movement, not basis points. */
export function marketPct(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}

/** The score's display scale: the 0..1 composite is shown as a whole number out of 100 ("0.826 →
 *  83"). One constant shared by the board, the tiles, and the quote page so "83" means the same
 *  thing everywhere it appears. */
export const SCORE_SCALE = 100;

/** 0..1 composite → whole-number score on the display scale. */
export function marketScore(score: number): number {
  return Math.round(score * SCORE_SCALE);
}

/** Decompose a revenue_trend ratio (steady = 1.0) into what the UI needs to draw: the signed
 *  delta, whether it is inside the flat band, and its direction. `trend` is already null-checked
 *  by callers — null means "no revenue to compare", which is a different statement from flat. */
export function trendDelta(trend: number): { delta: number; flat: boolean; up: boolean } {
  const delta = trend - 1;
  const flat = Math.abs(delta) < TREND_FLAT_BAND;
  return { delta, flat, up: delta > 0 };
}

/** Tenure in the largest honest unit. "428 days" is a number a viewer has to convert; the
 *  point of the field is "how long have they been trading", which months and years answer.
 *  Shared so the seller's card and the investor detail page phrase it identically. */
export function marketTenure(days: number): string {
  if (days < 1) return 'New today';
  if (days < 60) return `${Math.round(days)} day${Math.round(days) === 1 ? '' : 's'}`;
  if (days < 730) return `${Math.floor(days / 30)} months`;
  const years = Math.floor(days / 365);
  return `${years} year${years === 1 ? '' : 's'}`;
}

/** Turn the server's machine-readable gate reasons into one sentence a viewer can act on.
 *  The counts come from the server (orders_needed / days_needed), so the thresholds themselves
 *  are never duplicated in the client, where they could drift from the service constants.
 *  Shared by the seller's own card and the investor detail page — a thin file must read the
 *  same everywhere it appears. */
export function growthPrompt(data: {
  orders_needed: number;
  days_needed: number;
}): string {
  const parts: string[] = [];
  if (data.orders_needed > 0) {
    parts.push(`${data.orders_needed} more completed sale${data.orders_needed === 1 ? '' : 's'}`);
  }
  if (data.days_needed > 0) {
    parts.push(`${data.days_needed} more day${data.days_needed === 1 ? '' : 's'} of trading`);
  }
  if (parts.length === 0) return 'Building their funding score.';
  return `${parts.join(' and ')} to unlock their funding score.`;
}
