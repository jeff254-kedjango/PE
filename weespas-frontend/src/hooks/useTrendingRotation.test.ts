// Fake-timer tests for the per-slot decay engine. These pin the behavioural contract the product
// owner asked for: cards decay INDEPENDENTLY (any slot, not a FIFO scroll), no product is ever shown
// twice at once, every queued product gets airtime, a queue that fits never churns, and reduced
// motion freezes the board.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useTrendingRotation } from './useTrendingRotation';
import type { TrendingProductCard } from '../api/commerce';

function card(id: string, over: Partial<TrendingProductCard> = {}): TrendingProductCard {
  return {
    listing_id: id, seller_id: `sel-${id}`, title: `P-${id}`, price_cents: 1000, currency: 'KES',
    category: 'general', property_uuid: null, distance_m: 100, boost_tier: 'mtaa', image_url: null, ...over,
  };
}

function queueOf(n: number): TrendingProductCard[] {
  return Array.from({ length: n }, (_, i) => card(`l${i}`));
}

const ids = (cards: TrendingProductCard[]) => cards.map((c) => c.listing_id);

// Default: motion is allowed. Individual tests override matchMedia for the reduced-motion case.
function setReducedMotion(reduced: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: vi.fn().mockImplementation((q: string) => ({
      matches: reduced && q.includes('reduce'),
      media: q, onchange: null,
      addEventListener: vi.fn(), removeEventListener: vi.fn(),
      addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
    })),
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  setReducedMotion(false);
});
afterEach(() => {
  vi.useRealTimers();
});

describe('useTrendingRotation', () => {
  it('shows the whole queue and never cycles when it fits the slots', () => {
    const queue = queueOf(3);
    const { result } = renderHook(() => useTrendingRotation(queue, 12, 12));
    expect(ids(result.current).sort()).toEqual(['l0', 'l1', 'l2']);
    // Advance well past several slot lifetimes — nothing should churn.
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(ids(result.current).sort()).toEqual(['l0', 'l1', 'l2']);
  });

  it('never shows the same listing_id in two slots at once while cycling', () => {
    const queue = queueOf(20); // 20 > 4 slots → cycles
    const { result } = renderHook(() => useTrendingRotation(queue, 4, 12));
    expect(result.current).toHaveLength(4);
    for (let t = 0; t < 30; t += 1) {
      act(() => { vi.advanceTimersByTime(3_000); });
      const visible = ids(result.current);
      expect(new Set(visible).size).toBe(visible.length); // no duplicate on screen
      expect(visible).toHaveLength(4);
    }
  });

  it('decays slots independently — never the whole board in one batch tick', () => {
    // The product-owner's core ask: cards must NOT flip in synchronized batches ("a batch for 5s,
    // then the next batch"). With per-listing varied lifetimes, deadlines spread out and re-up on
    // their own clocks, so no single 1s tick should ever turn over every slot at once.
    const queue = queueOf(10);
    const { result } = renderHook(() => useTrendingRotation(queue, 4, 12));
    let prev = ids(result.current);
    let totalFlips = 0;
    const flipTicks: number[] = [];
    let maxFlipsInOneTick = 0;
    // Walk 60s in 1s steps, counting how many slots change each tick.
    for (let t = 1; t <= 60; t += 1) {
      act(() => { vi.advanceTimersByTime(1_000); });
      const cur = ids(result.current);
      const flips = cur.filter((id, i) => id !== prev[i]).length;
      if (flips > 0) { flipTicks.push(t); totalFlips += flips; }
      maxFlipsInOneTick = Math.max(maxFlipsInOneTick, flips);
      prev = cur;
    }
    // Cards DID rotate (queue 10 > 4 slots), across MULTIPLE distinct moments (independent clocks),
    // and crucially NEVER all 4 at once (that would be the batch behaviour the owner rejected).
    expect(totalFlips).toBeGreaterThanOrEqual(2);
    expect(flipTicks.length).toBeGreaterThanOrEqual(2);
    expect(maxFlipsInOneTick).toBeLessThan(4);
  });

  it('gives every queued product airtime over a full cycle', () => {
    const queue = queueOf(8);
    const { result } = renderHook(() => useTrendingRotation(queue, 3, 12));
    const seen = new Set<string>(ids(result.current));
    for (let t = 0; t < 40; t += 1) {
      act(() => { vi.advanceTimersByTime(3_000); });
      ids(result.current).forEach((id) => seen.add(id));
    }
    expect(seen.size).toBe(8); // everyone has appeared
  });

  it('advances a slot immediately when a poll drops its visible listing', () => {
    let queue = queueOf(6);
    const { result, rerender } = renderHook(
      ({ q }: { q: TrendingProductCard[] }) => useTrendingRotation(q, 3, 12),
      { initialProps: { q: queue } },
    );
    const before = ids(result.current);
    const dropped = before[0];
    // New poll: the first visible listing is gone from the queue entirely.
    queue = queue.filter((c) => c.listing_id !== dropped);
    act(() => { rerender({ q: queue }); });
    const after = ids(result.current);
    expect(after).not.toContain(dropped);
    expect(new Set(after).size).toBe(after.length);
    expect(after).toHaveLength(3);
  });

  it('freezes (no churn) under prefers-reduced-motion', () => {
    setReducedMotion(true);
    const queue = queueOf(20);
    const { result } = renderHook(() => useTrendingRotation(queue, 4, 12));
    const initial = ids(result.current);
    expect(initial).toHaveLength(4);
    act(() => { vi.advanceTimersByTime(120_000); });
    expect(ids(result.current)).toEqual(initial); // identical, frozen
  });

  it('pauses decay while paused=true and resumes after', () => {
    const queue = queueOf(20);
    const { result, rerender } = renderHook(
      ({ p }: { p: boolean }) => useTrendingRotation(queue, 4, 12, p),
      { initialProps: { p: true } },
    );
    const initial = ids(result.current);
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(ids(result.current)).toEqual(initial); // paused → no swaps
    act(() => { rerender({ p: false }); });
    // Lifetimes are now per-listing jittered in [slotMs, 2*slotMs) atop a per-slot phase, so the
    // latest possible first deadline ≈ phase(9s) + life(<24s) < 33s. Advance past that to guarantee
    // at least one slot has decayed once resumed.
    act(() => { vi.advanceTimersByTime(35_000); });
    expect(ids(result.current)).not.toEqual(initial);
  });

  it('returns [] for an empty queue', () => {
    const { result } = renderHook(() => useTrendingRotation([], 12, 12));
    expect(result.current).toEqual([]);
  });
});
