// Tests for the fit-count math + the ResizeObserver-backed hook. The pure computeFit is the load-
// bearing logic (how many fixed slots a viewport gets); the hook test confirms it reads clientHeight
// and clamps. A mock ResizeObserver stands in for jsdom (which has none).
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { createRef } from 'react';
import { computeFit, useFitCount } from './useFitCount';

describe('computeFit', () => {
  // Measured row footprint on the live rail: 59px card + 12px gap.
  const opts = { rowHeight: 59, gap: 12 };

  it('floors to the number of rows that fit (N rows = N*row + (N-1)*gap)', () => {
    // 707px board (1440x900 viewport, measured) → 10 rows: 10*59 + 9*12 = 698 <= 707; 11 would be 769.
    expect(computeFit(707, opts)).toBe(10);
    // 527px (1280x720) → 7: 7*59 + 6*12 = 485 <= 527; 8 = 556 > 527.
    expect(computeFit(527, opts)).toBe(7);
    // 857px (1680x1050) → 12.
    expect(computeFit(857, opts)).toBe(12);
  });

  it('clamps to [min, max]', () => {
    expect(computeFit(857, { ...opts, max: 8 })).toBe(8);   // capped by server candidate cap
    expect(computeFit(10, { ...opts, min: 1 })).toBe(1);    // tiny board → still 1
    expect(computeFit(0, { ...opts, min: 2 })).toBe(2);     // unmeasured → min
  });

  it('is safe for degenerate inputs', () => {
    expect(computeFit(500, { rowHeight: 0, gap: 12, min: 3 })).toBe(3);
    expect(computeFit(-5, { rowHeight: 59, gap: 12, min: 1 })).toBe(1);
  });
});

describe('useFitCount', () => {
  let observed: Element | null;
  beforeEach(() => {
    observed = null;
    // Minimal ResizeObserver mock: record the observed element; we don't need to fire it because the
    // hook does an initial synchronous measure on mount.
    vi.stubGlobal('ResizeObserver', class {
      observe(el: Element) { observed = el; }
      unobserve() {}
      disconnect() {}
    });
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { cb(0); return 1; });
    vi.stubGlobal('cancelAnimationFrame', () => {});
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('measures clientHeight and returns the fitted, capped count', () => {
    const el = document.createElement('ul');
    Object.defineProperty(el, 'clientHeight', { value: 707, configurable: true });
    const ref = createRef<HTMLUListElement>();
    // @ts-expect-error assign for the test (ref is read-only in types but mutable at runtime)
    ref.current = el;
    const { result } = renderHook(() => useFitCount(ref, { rowHeight: 59, gap: 12, min: 1, max: 12 }));
    expect(result.current).toBe(10);
    expect(observed).toBe(el);
  });
});
