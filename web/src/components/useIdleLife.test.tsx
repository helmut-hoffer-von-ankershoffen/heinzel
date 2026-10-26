import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IDLE_MAX_MS, IDLE_MIN_MS, IDLE_MOVES, nextIdleMove, useIdleLife } from "./useIdleLife";

let reduced = false;
const listeners = new Set<() => void>();

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: hidden ? "hidden" : "visible" });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers();
  // The shortest wait and the first move, so a test knows exactly when the move shows.
  vi.spyOn(Math, "random").mockReturnValue(0);
  reduced = false;
  listeners.clear();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  window.matchMedia = ((query: string) => ({
    media: query,
    get matches() {
      return reduced;
    },
    addEventListener: (_: string, fn: () => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
  })) as unknown as typeof window.matchMedia;
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("useIdleLife", () => {
  it("does not choose the same move twice in a row when alternatives exist", () => {
    expect(nextIdleMove(["blink", "tilt"], "blink", () => 0)).toBe("tilt");
    expect(nextIdleMove(["blink"], "blink", () => 0)).toBe("blink");
    expect(nextIdleMove([], null, () => 0)).toBeNull();
  });
  it("plays a move 8 to 30 seconds in, then rests again", () => {
    const { result } = renderHook(() => useIdleLife(true));
    expect(result.current).toBeNull();
    act(() => vi.advanceTimersByTime(IDLE_MIN_MS - 1));
    expect(result.current).toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(IDLE_MOVES).toContain(result.current);
    act(() => vi.advanceTimersByTime(2_000));
    expect(result.current).toBeNull();
  });

  it("keeps coming back with more moves", () => {
    const seen: unknown[] = [];
    const { result } = renderHook(() => useIdleLife(true, ["blink"]));
    for (let i = 0; i < 3; i++) {
      // The next wait starts when a move starts, so after the 2 s of rest 6 s remain.
      act(() => vi.advanceTimersByTime(i === 0 ? IDLE_MIN_MS : IDLE_MIN_MS - 2_000));
      seen.push(result.current);
      act(() => vi.advanceTimersByTime(2_000));
    }
    expect(seen).toEqual(["blink", "blink", "blink"]);
  });

  it("stays still when playful is off", () => {
    const { result } = renderHook(() => useIdleLife(false));
    act(() => vi.advanceTimersByTime(IDLE_MAX_MS * 5));
    expect(result.current).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("stays still under prefers-reduced-motion, and stops when it turns on", () => {
    reduced = true;
    const { result } = renderHook(() => useIdleLife(true));
    act(() => vi.advanceTimersByTime(IDLE_MAX_MS * 5));
    expect(result.current).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
    reduced = false;
    act(() => {
      for (const fn of listeners) fn();
    });
    act(() => vi.advanceTimersByTime(IDLE_MIN_MS));
    expect(result.current).not.toBeNull();
    reduced = true;
    act(() => {
      for (const fn of listeners) fn();
    });
    expect(result.current).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("stays still while the tab is hidden and resumes when it is shown", () => {
    setHidden(true);
    const { result } = renderHook(() => useIdleLife(true));
    act(() => vi.advanceTimersByTime(IDLE_MAX_MS * 5));
    expect(result.current).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
    act(() => setHidden(false));
    act(() => vi.advanceTimersByTime(IDLE_MIN_MS));
    expect(result.current).not.toBeNull();
    act(() => setHidden(true));
    expect(result.current).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("clears its timers on unmount", () => {
    const { unmount } = renderHook(() => useIdleLife(true));
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
