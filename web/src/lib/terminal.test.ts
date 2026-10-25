import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { followSize } from "./terminal";

let notify: () => void = () => {};
class FakeObserver {
  constructor(callback: () => void) {
    notify = callback;
  }
  observe() {}
  disconnect() {}
}

describe("followSize", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("ResizeObserver", FakeObserver);
    vi.stubGlobal("requestAnimationFrame", (cb: () => void) => setTimeout(cb, 0) as unknown as number);
    vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("fits, redraws and sends the new size once a container resize settles", () => {
    const element = { offsetParent: {} } as unknown as HTMLElement;
    const terminal = { cols: 120, rows: 30, refresh: vi.fn() };
    const fit = { fit: vi.fn() };
    const send = vi.fn();
    const follower = followSize(element, terminal, fit, send);
    notify();
    notify();
    vi.advanceTimersByTime(100);
    expect(fit.fit).toHaveBeenCalledTimes(1);
    expect(terminal.refresh).toHaveBeenCalledWith(0, 29);
    expect(send).toHaveBeenCalledWith(120, 30);
    window.dispatchEvent(new Event("resize"));
    vi.advanceTimersByTime(100);
    expect(fit.fit).toHaveBeenCalledTimes(2);
    follower.dispose();
  });

  it("waits while the container is hidden", () => {
    const element = { offsetParent: null } as unknown as HTMLElement;
    const fit = { fit: vi.fn() };
    const send = vi.fn();
    followSize(element, { cols: 80, rows: 24, refresh: vi.fn() }, fit, send).refit();
    vi.advanceTimersByTime(100);
    expect(fit.fit).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
  });
});
