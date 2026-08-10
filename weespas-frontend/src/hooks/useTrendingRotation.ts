// useTrendingRotation — the §8 trending board's per-slot decay engine.
//
// The server returns the full QUEUE of boosted product cards for the locality; this hook turns it
// into a fixed set of `visibleSlots` cards where EACH slot decays on its OWN independent timer and,
// when it decays, the next queued product (one not already on screen) takes its place. Any slot can
// flip independently — it is NOT a synchronized FIFO scroll (which frees only one slot per tick and
// starves the queue), and it is NOT a batch flip (all slots turning over together every N seconds).
// Every queued product gets fair airtime via a shared next-pointer that skips whatever is visible.
//
// INDEPENDENT LIFECYCLES (the product-owner's core ask): real boosts are created at DIFFERENT times,
// so the board must look like cards arriving/leaving on their own clocks — never "a batch shows for
// 5s, then the next batch". Two mechanisms guarantee that:
//   * Each card's lifetime is a PER-LISTING jittered value in [slotMs, 2*slotMs) derived from a
//     stable hash of its listing_id (NOT slot index, NOT a shared slotMs). So when a fresh card
//     lands in a slot it adopts that card's own lifetime — different cards decay at different rates
//     and never re-converge onto one cadence.
//   * The INITIAL board is phased per slot index too, so even the very first wave is staggered.
// Both are pure functions (no Math.random()/Date.now()), so vi.useFakeTimers() stays deterministic.
// All lifetimes are kept > 5s (the slotSeconds floor is enforced server-side and re-clamped here).
//
// Design properties (these are the ones the tests pin):
//   * SINGLE scheduler. One self-scheduling setTimeout fires at the nearest slot deadline; on fire
//     it advances EVERY slot whose deadline has passed, in one functional state update. There are
//     no N independent timers mutating shared state, so two near-simultaneous deadlines can never
//     both read the same "visible set" snapshot and pick the SAME product (the duplication race).
//   * Reconcile by `listing_id`, never array index (the queue re-sorts by tier between polls). A
//     visible product dropped from a fresh queue is replaced immediately.
//   * No cycling when the whole queue fits (active <= visibleSlots): cards persist, nothing churns.
//   * prefers-reduced-motion: freeze — render the first `visibleSlots` products statically (content
//     silently replacing itself is itself motion/disorientation).
//
// Cost: O(visibleSlots) per scheduler fire (bounded, tiny); one timer total. The "skip visible"
// advance is O(visibleSlots) worst case (amortized O(1)) — bounded, not the O(n) the FIFO would be.
import { useEffect, useRef, useState } from 'react';
import type { TrendingProductCard } from '../api/commerce';

// A virtual clock: we track elapsed ms via timers rather than reading Date.now(), so fake timers
// fully control progression. `at` is the elapsed-ms deadline when a slot next decays.
interface Slot {
  listingId: string;
  deadlineMs: number;
}

interface RotationState {
  // The elapsed-ms clock (advanced only by the scheduler), kept in state so a fire re-renders.
  nowMs: number;
  slots: Slot[];
  // Index into `queue` for the next product to pull (advances past currently-visible ids).
  pointer: number;
}

/** Read the user's reduced-motion preference once (SSR-safe). A change mid-session is rare enough
 *  that we don't subscribe — the rail re-mounts on navigation. */
function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

/** A small stable hash of a listing_id → an integer in [0, 1000). Pure (no Date/random) so fake
 *  timers stay deterministic, but varied enough that different ids land on different lifetimes. */
function idHash(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i += 1) {
    h = (h * 31 + id.charCodeAt(i)) | 0; // 32-bit rolling hash
  }
  return Math.abs(h) % 1000;
}

/** Per-CARD lifetime in ms: a jittered value in [slotMs, 2*slotMs) keyed off the listing_id, so
 *  each product holds its slot for its OWN duration. This is what makes cards decay on independent
 *  clocks rather than in lockstep batches. Floor is slotMs (server-clamped > 5s), so every card is
 *  always readable. The jitter spreads deadlines across a full extra slotMs window. */
function cardLifetimeMs(listingId: string, slotMs: number): number {
  return slotMs + Math.round((idHash(listingId) / 1000) * slotMs);
}

/** Find the next queue index (starting at `from`, wrapping) whose listing_id is NOT in `visible`.
 *  Returns -1 only if every queue item is already visible (queue.length <= visible.size). Bounded
 *  by queue.length; in practice it walks at most `visible.size + 1` before landing. */
function nextFreeIndex(
  queue: TrendingProductCard[],
  from: number,
  visible: Set<string>,
): number {
  for (let step = 0; step < queue.length; step += 1) {
    const idx = (from + step) % queue.length;
    if (!visible.has(queue[idx].listing_id)) return idx;
  }
  return -1;
}

/** Build the initial slot set: the first `slots` queue items. Each slot's first decay is the sum of
 *  a per-index PHASE (so the opening wave is staggered, not lockstep) and the card's OWN per-listing
 *  lifetime (so from the very start cards are on independent clocks). Pure fn of index + id. */
function initialState(queue: TrendingProductCard[], slots: number, slotMs: number): RotationState {
  const n = Math.min(slots, queue.length);
  const out: Slot[] = [];
  for (let i = 0; i < n; i += 1) {
    // phase_i in [0, slotMs): a rolling offset so slots don't all reach their first deadline at once.
    const phase = slots > 0 ? Math.round((slotMs * i) / slots) : 0;
    const life = cardLifetimeMs(queue[i].listing_id, slotMs);
    out.push({ listingId: queue[i].listing_id, deadlineMs: phase + life });
  }
  return { nowMs: 0, slots: out, pointer: n };
}

/**
 * Turn a product queue into a self-rotating set of visible cards.
 *
 * @param queue        full ordered queue from the server (already deduped by listing_id)
 * @param visibleSlots how many cards to show at once
 * @param slotSeconds  per-card lifetime (server-tuned by contention; always > 5s)
 * @param paused       when true, the decay is frozen (e.g. the buyer is hovering/reading a card) so
 *                     a card never vanishes mid-interaction. Resumes where it left off.
 * @returns the cards to render right now (length = min(visibleSlots, queue.length))
 */
export function useTrendingRotation(
  queue: TrendingProductCard[],
  visibleSlots: number,
  slotSeconds: number,
  paused: boolean = false,
): TrendingProductCard[] {
  const slotMs = Math.max(1, slotSeconds) * 1000;
  const slots = Math.max(0, Math.floor(visibleSlots));
  const reduceMotion = useRef(prefersReducedMotion()).current;

  // Fast index from listing_id → card for the current queue (rebuilt only when the queue changes).
  const byId = new Map(queue.map((c) => [c.listing_id, c]));

  // Whether the queue exceeds the visible slots — only then do we cycle.
  const cycling = !reduceMotion && queue.length > slots && slots > 0;

  const [state, setState] = useState<RotationState>(() => initialState(queue, slots, slotMs));

  // ---- Reconcile on queue / config change (poll brought a new queue, or slots/slotSeconds moved).
  // Keep slots whose listing_id is still present (preserve their running deadline); replace dropped
  // ones immediately; (re)fill up to `slots`. Identity is by listing_id, never index.
  const queueKey = queue.map((c) => c.listing_id).join('|');
  useEffect(() => {
    setState((prev) => {
      const present = new Set(byId.keys());
      const visible = new Set<string>();
      const kept: Slot[] = [];
      // 1) keep still-present visible slots, in their current screen order.
      for (const s of prev.slots) {
        if (present.has(s.listingId) && !visible.has(s.listingId)) {
          kept.push(s);
          visible.add(s.listingId);
        }
      }
      // 2) (re)fill up to min(slots, queue.length) with not-yet-visible queue items.
      let pointer = 0;
      const target = Math.min(slots, queue.length);
      while (kept.length < target) {
        const idx = nextFreeIndex(queue, pointer, visible);
        if (idx < 0) break;
        const lid = queue[idx].listing_id;
        kept.push({ listingId: lid, deadlineMs: prev.nowMs + cardLifetimeMs(lid, slotMs) });
        visible.add(lid);
        pointer = idx + 1;
      }
      // Trim if slots shrank.
      const trimmed = kept.slice(0, target);
      return { nowMs: prev.nowMs, slots: trimmed, pointer };
    });
    // queueKey captures membership+order; slots/slotMs capture config.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queueKey, slots, slotMs]);

  // ---- The single scheduler. Sleep until the nearest slot deadline, then advance every slot whose
  // deadline has passed (one functional update). Re-arms itself off the freshly-computed state.
  useEffect(() => {
    if (!cycling || paused) return; // nothing to rotate (fits on screen / reduced motion / paused)
    let timer: ReturnType<typeof setTimeout> | undefined;

    const arm = () => {
      setState((prev) => {
        if (prev.slots.length === 0) return prev;
        const soonest = Math.min(...prev.slots.map((s) => s.deadlineMs));
        const now = Math.max(prev.nowMs, soonest);

        // Advance any slot due at/under `now`. `visible` tracks every id we must NOT land on: it
        // starts as all currently-shown ids and grows as we pick replacements. Crucially we do NOT
        // remove a decaying slot's own id before searching — otherwise a replacement could re-pick
        // the very card that just decayed (and with a freshly-reconciled pointer at 0 it would, so
        // the slot would "decay into itself" and never visibly change). Keeping it in `visible`
        // forces a genuinely new card; the old id simply drops out when we overwrite the slot.
        const visible = new Set(prev.slots.map((s) => s.listingId));
        let pointer = prev.pointer;
        const nextSlots = prev.slots.map((s) => {
          if (s.deadlineMs > now) return s;
          const idx = nextFreeIndex(queue, pointer, visible);
          if (idx < 0) {
            // Everything else is already on screen (queue == visible): keep the card, push its
            // deadline out by ITS OWN lifetime (not a shared slotMs) so it stays on its own clock.
            return { ...s, deadlineMs: now + cardLifetimeMs(s.listingId, slotMs) };
          }
          const picked = queue[idx];
          visible.add(picked.listing_id);
          pointer = idx + 1;
          // The new card adopts ITS OWN lifetime → independent, non-converging decay.
          return { listingId: picked.listing_id, deadlineMs: now + cardLifetimeMs(picked.listing_id, slotMs) };
        });
        return { nowMs: now, slots: nextSlots, pointer };
      });
    };

    // Compute the delay to the nearest deadline from the CURRENT state without reading it twice:
    // we schedule via a 0-arg tick that re-arms. To keep delays accurate we read `state` here.
    const soonest = state.slots.length
      ? Math.min(...state.slots.map((s) => s.deadlineMs))
      : state.nowMs + slotMs;
    const delay = Math.max(0, soonest - state.nowMs);
    timer = setTimeout(arm, delay);
    return () => { if (timer) clearTimeout(timer); };
    // Re-arm whenever the clock or slots advance (each `arm` bumps nowMs), or config/queue changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cycling, paused, state.nowMs, queueKey, slotMs]);

  // ---- Project state → the cards to render. Reduced-motion / non-cycling: first N queue items.
  if (!cycling) {
    return queue.slice(0, Math.min(slots, queue.length));
  }
  // Map current slots → cards, dropping any whose id vanished mid-reconcile (defensive).
  return state.slots
    .map((s) => byId.get(s.listingId))
    .filter((c): c is TrendingProductCard => c !== undefined);
}
