import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Drives the one-shot "pop" feedback shared by every like/favourite heart in the app
 * (property FavoriteButton, trade ProductCard "Save", comment likes). Call `pop()` on toggle;
 * `popping` is true for the duration of the animation, during which the caller applies the
 * `animate-heart` class (see styles/animations.css → @keyframes heartPop).
 *
 * The animation is event-driven (fires once per tap, never loops) and is automatically
 * disabled by the global `prefers-reduced-motion` guard in styles/reset.css — so there is no
 * per-component accessibility handling to maintain.
 *
 * The pending timer is cleared on unmount and re-pop, so a heart that unmounts mid-animation
 * (e.g. a feed row scrolled out of the virtualised list) never calls setState on a dead component.
 */
const HEART_POP_MS = 400; // must match @keyframes heartPop duration in animations.css

export default function useHeartPop(durationMs: number = HEART_POP_MS) {
  const [popping, setPopping] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clear = useCallback(() => {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const pop = useCallback(() => {
    clear(); // restart cleanly if tapped again mid-animation
    setPopping(true);
    timer.current = setTimeout(() => {
      setPopping(false);
      timer.current = null;
    }, durationMs);
  }, [clear, durationMs]);

  useEffect(() => clear, [clear]); // clear any pending timer on unmount

  return { popping, pop };
}
