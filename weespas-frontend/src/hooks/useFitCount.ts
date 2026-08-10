// useFitCount — how many fixed-height rows fit inside a container, recomputed only when the
// container actually resizes.
//
// The trending board (§8) shows a FIXED number of slots with NO scroll: exactly as many product
// cards as fit the rail height at the current viewport. That count differs per screen (a 720px-tall
// laptop fits ~7, a 1050px screen fits ~12), so we measure the live board height with a single
// ResizeObserver and derive `floor((height + gap) / (rowHeight + gap))`.
//
// Performance: a ResizeObserver fires only on actual size changes (mount, window resize, zoom) — NOT
// per frame and NOT on scroll. We also coalesce bursts (the observer can fire several times during a
// drag) into one state update via requestAnimationFrame, and only setState when the integer count
// changes, so a continuous resize re-renders at most once per distinct count. O(1) per fire.
import { useEffect, useState } from 'react';

interface FitOptions {
  /** Rendered height of one row in px (card height). */
  rowHeight: number;
  /** Vertical gap between rows in px. */
  gap: number;
  /** Never return fewer than this (so the board is never empty when there IS room). */
  min?: number;
  /** Never return more than this (the server's candidate cap — no point measuring beyond it). */
  max?: number;
}

/** Pure fit math, exported for unit testing without a DOM. */
export function computeFit(
  containerHeight: number,
  { rowHeight, gap, min = 1, max = Infinity }: FitOptions,
): number {
  if (rowHeight <= 0 || containerHeight <= 0) return min;
  // N rows occupy N*rowHeight + (N-1)*gap; solving N <= (H + gap) / (rowHeight + gap).
  const n = Math.floor((containerHeight + gap) / (rowHeight + gap));
  return Math.max(min, Math.min(max, n));
}

/**
 * Observe `ref`'s height and return how many `rowHeight`-tall rows fit. Recomputes only when the
 * measured count changes.
 *
 * @returns the fitted row count (clamped to [min, max]); `min` until the first measurement lands.
 */
export function useFitCount(
  ref: React.RefObject<HTMLElement>,
  opts: FitOptions,
): number {
  const { rowHeight, gap, min = 1, max } = opts;
  const [count, setCount] = useState(min);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;

    let raf = 0;
    const measure = () => {
      raf = 0;
      // clientHeight includes the element's own padding; the rows only get the space INSIDE that
      // padding, so subtract it. Reading it from computed style keeps the math correct even if the
      // board padding changes in CSS (no hardcoded constant to drift). Border is already excluded.
      const cs = getComputedStyle(el);
      const padY = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
      const usable = el.clientHeight - padY;
      const next = computeFit(usable, { rowHeight, gap, min, max });
      setCount((prev) => (prev === next ? prev : next));
    };
    // Coalesce observer bursts into one rAF-batched measure.
    const schedule = () => { if (!raf) raf = requestAnimationFrame(measure); };

    const ro = new ResizeObserver(schedule);
    ro.observe(el);
    measure(); // initial synchronous measurement

    return () => {
      ro.disconnect();
      if (raf) cancelAnimationFrame(raf);
    };
    // rowHeight/gap/min/max are primitives; re-run if the row metrics change.
  }, [ref, rowHeight, gap, min, max]);

  return count;
}
