import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import useHeartPop from './useHeartPop';

describe('useHeartPop', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('is not popping initially, pops on call, then settles after the duration', () => {
    const { result } = renderHook(() => useHeartPop(400));
    expect(result.current.popping).toBe(false);

    act(() => result.current.pop());
    expect(result.current.popping).toBe(true);

    act(() => vi.advanceTimersByTime(399));
    expect(result.current.popping).toBe(true); // still within the window

    act(() => vi.advanceTimersByTime(1));
    expect(result.current.popping).toBe(false); // settled exactly at the duration
  });

  it('re-popping mid-animation restarts the timer (does not settle early)', () => {
    const { result } = renderHook(() => useHeartPop(400));
    act(() => result.current.pop());
    act(() => vi.advanceTimersByTime(300));
    act(() => result.current.pop()); // restart at t=300
    act(() => vi.advanceTimersByTime(200)); // original would have fired at 400; restarted fires at 700
    expect(result.current.popping).toBe(true);
    act(() => vi.advanceTimersByTime(200));
    expect(result.current.popping).toBe(false);
  });

  it('clears the pending timer on unmount (no setState after teardown)', () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { result, unmount } = renderHook(() => useHeartPop(400));
    act(() => result.current.pop());
    unmount();
    // Advancing past the duration must not fire a setState on the unmounted hook.
    act(() => vi.advanceTimersByTime(400));
    expect(errSpy).not.toHaveBeenCalled();
    errSpy.mockRestore();
  });
});
