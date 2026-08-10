import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Track which item index is currently "active" (most visible) inside a
 * vertical snap-scroll feed using a single shared IntersectionObserver.
 *
 * Design points:
 * - ONE IntersectionObserver for the whole feed (per-item observers don't scale).
 * - `register` identity is stable across activeIndex changes, so child effects
 *   that depend on it don't re-run on every scroll tick.
 * - The "active index" is the registered index with the highest intersection
 *   ratio at or above `threshold`. Ties prefer the lower index (deterministic).
 */
export interface VisibleIndexController {
  register: (index: number, node: HTMLElement | null) => void;
  activeIndex: number;
}

export function useActiveIndex(itemCount: number, threshold = 0.6): VisibleIndexController {
  const [activeIndex, setActiveIndex] = useState(0);
  const nodesRef = useRef<Map<number, HTMLElement>>(new Map());
  const ratiosRef = useRef<Map<number, number>>(new Map());
  const observerRef = useRef<IntersectionObserver | null>(null);
  const thresholdRef = useRef(threshold);
  thresholdRef.current = threshold;

  useEffect(() => {
    const ratios = ratiosRef.current;
    const nodes = nodesRef.current;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const el = entry.target as HTMLElement;
          const idx = Number(el.dataset.shortIndex);
          if (!Number.isFinite(idx)) continue;
          ratios.set(idx, entry.intersectionRatio);
        }
        let bestIdx = -1;
        let bestRatio = thresholdRef.current;
        ratios.forEach((ratio, idx) => {
          if (ratio > bestRatio || (ratio === bestRatio && (bestIdx < 0 || idx < bestIdx))) {
            bestRatio = ratio;
            bestIdx = idx;
          }
        });
        if (bestIdx >= 0) {
          setActiveIndex((prev) => (prev === bestIdx ? prev : bestIdx));
        }
      },
      { threshold: [0, 0.25, 0.5, 0.75, 1] },
    );
    observerRef.current = observer;
    // Re-observe any nodes registered before the observer was ready.
    nodes.forEach((node) => observer.observe(node));
    return () => {
      observer.disconnect();
      observerRef.current = null;
    };
  }, []);

  // Stable register fn — identity does NOT change when activeIndex updates.
  const register = useCallback((index: number, node: HTMLElement | null) => {
    const nodes = nodesRef.current;
    const ratios = ratiosRef.current;
    const prev = nodes.get(index);
    if (prev && prev !== node) {
      observerRef.current?.unobserve(prev);
      nodes.delete(index);
      ratios.delete(index);
    }
    if (node) {
      node.dataset.shortIndex = String(index);
      nodes.set(index, node);
      observerRef.current?.observe(node);
    }
  }, []);

  // Drop stale entries when the list shrinks so they can't win the ratio race.
  useEffect(() => {
    const ratios = ratiosRef.current;
    const nodes = nodesRef.current;
    ratios.forEach((_, idx) => {
      if (idx >= itemCount) ratios.delete(idx);
    });
    nodes.forEach((node, idx) => {
      if (idx >= itemCount) {
        observerRef.current?.unobserve(node);
        nodes.delete(idx);
      }
    });
  }, [itemCount]);

  return { register, activeIndex };
}
