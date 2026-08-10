import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useMediaQuery } from './useMediaQuery';

type Listener = (e: MediaQueryListEvent) => void;

/** Install a controllable window.matchMedia mock; returns a flip() to simulate
 *  a viewport change and the add/remove spies so we can assert cleanup. */
function installMatchMedia(initial: boolean) {
  let matches = initial;
  const listeners = new Set<Listener>();
  const add = vi.fn((_: string, cb: Listener) => listeners.add(cb));
  const remove = vi.fn((_: string, cb: Listener) => listeners.delete(cb));
  window.matchMedia = vi.fn().mockImplementation(() => ({
    get matches() { return matches; },
    media: '',
    addEventListener: add,
    removeEventListener: remove,
  })) as unknown as typeof window.matchMedia;
  return {
    add,
    remove,
    flip(next: boolean) {
      matches = next;
      listeners.forEach((cb) => cb({ matches } as MediaQueryListEvent));
    },
  };
}

afterEach(() => { vi.restoreAllMocks(); });

describe('useMediaQuery', () => {
  it('returns the initial match synchronously', () => {
    installMatchMedia(true);
    const { result } = renderHook(() => useMediaQuery('(min-width: 768px)'));
    expect(result.current).toBe(true);
  });

  it('updates when the query flips', () => {
    const mm = installMatchMedia(false);
    const { result } = renderHook(() => useMediaQuery('(min-width: 768px)'));
    expect(result.current).toBe(false);
    act(() => mm.flip(true));
    expect(result.current).toBe(true);
  });

  it('removes its listener on unmount', () => {
    const mm = installMatchMedia(true);
    const { unmount } = renderHook(() => useMediaQuery('(min-width: 768px)'));
    expect(mm.add).toHaveBeenCalledTimes(1);
    unmount();
    expect(mm.remove).toHaveBeenCalledTimes(1);
  });
});
