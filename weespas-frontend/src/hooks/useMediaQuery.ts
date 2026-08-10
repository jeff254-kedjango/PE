import { useEffect, useState } from 'react';

/**
 * Subscribe to a CSS media query and re-render when it flips.
 *
 * O(1): one `matchMedia` object + one `change` listener, cleaned up on unmount
 * or when `query` changes — no resize loop, no per-frame work. The initial
 * value is read synchronously from `matchMedia().matches` so there is no
 * first-paint flash (this is a client-only Vite SPA — `window` always exists).
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);

  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches);
    // Resync in case `query` changed between render and effect.
    setMatches(mql.matches);
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [query]);

  return matches;
}
