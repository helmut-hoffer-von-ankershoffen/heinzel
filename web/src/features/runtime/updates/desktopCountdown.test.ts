import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { localMacClient, useDesktopCountdown } from "./desktopCountdown";

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("starts at 30 seconds and restarts only once across two windows", () => {
  const restart = vi.fn();
  const first = renderHook(() => useDesktopCountdown("native:0.9.58:0.9.59", true, true, restart));
  const second = renderHook(() => useDesktopCountdown("native:0.9.58:0.9.59", true, true, restart));
  expect(first.result.current.seconds).toBe(30);
  expect(second.result.current.seconds).toBe(30);
  act(() => vi.advanceTimersByTime(29_000));
  expect(first.result.current.seconds).toBe(1);
  expect(restart).not.toHaveBeenCalled();
  act(() => vi.advanceTimersByTime(1_000));
  expect(restart).toHaveBeenCalledTimes(1);
});

it("keeps a cancelled restart manual across reloads but offers a subsequent version", () => {
  const restart = vi.fn();
  const first = renderHook(() => useDesktopCountdown("electron:0.9.58:0.9.59", true, true, restart));
  act(() => first.result.current.cancel());
  first.unmount();
  const reloaded = renderHook(() => useDesktopCountdown("electron:0.9.58:0.9.59", true, true, restart));
  expect(reloaded.result.current.manual).toBe(true);
  act(() => vi.advanceTimersByTime(60_000));
  expect(restart).not.toHaveBeenCalled();
  const next = renderHook(() => useDesktopCountdown("electron:0.9.58:0.9.60", true, true, restart));
  expect(next.result.current.seconds).toBe(30);
});

it("waits for idle and does not restart after automatic updates are disabled", () => {
  const restart = vi.fn();
  const page = renderHook(({ enabled, idle }) => useDesktopCountdown("native:test", enabled, idle, restart), {
    initialProps: { enabled: true, idle: false },
  });
  act(() => vi.advanceTimersByTime(35_000));
  expect(page.result.current.seconds).toBeNull();
  page.rerender({ enabled: true, idle: true });
  expect(page.result.current.seconds).toBe(30);
  page.rerender({ enabled: false, idle: true });
  act(() => vi.advanceTimersByTime(35_000));
  expect(restart).not.toHaveBeenCalled();
  page.rerender({ enabled: true, idle: true });
  expect(page.result.current.seconds).toBe(30);
  expect(restart).not.toHaveBeenCalled();
});

it("recognizes the published native Mac bridge before clientOS was added", () => {
  vi.stubGlobal("heinzelDesktop", { clientFlavor: "native", lifecycle: () => {} });
  expect(localMacClient()).toBe("native");
  vi.stubGlobal("heinzelDesktop", { clientFlavor: "native", mobileWorkspace: true });
  expect(localMacClient()).toBeNull();
});

it("does not use a remote workspace or an iPhone as a Mac update authority", () => {
  vi.stubGlobal("heinzelDesktop", { clientOS: "macOS", clientFlavor: "electron", remoteWorkspace: true });
  expect(localMacClient()).toBeNull();
  vi.stubGlobal("heinzelDesktop", { clientOS: "iOS", clientFlavor: "native" });
  expect(localMacClient()).toBeNull();
});

it("keeps the timer and cancellation when storage is readable but full", () => {
  const write = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("Full", "QuotaExceededError");
  });
  const restart = vi.fn();
  try {
    const timer = renderHook(() => useDesktopCountdown("native:full-storage", true, true, restart));
    act(() => vi.advanceTimersByTime(10_000));
    expect(timer.result.current.seconds).toBe(20);
    act(() => timer.result.current.cancel());
    act(() => vi.advanceTimersByTime(30_000));
    expect(timer.result.current.manual).toBe(true);
    expect(restart).not.toHaveBeenCalled();
  } finally {
    write.mockRestore();
  }
});
