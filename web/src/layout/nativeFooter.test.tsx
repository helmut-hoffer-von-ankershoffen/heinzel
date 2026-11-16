import { act, render, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { NativeFooterSlot, nativeFooter } from "./nativeFooter";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.documentElement.style.removeProperty("--hz-viewport-safe-bottom");
});

it("requires the new bridge and clamps finite native metrics", () => {
  expect(nativeFooter()).toBeNull();
  const bridge = { metrics: () => ({ width: 500, bottom: -1 }), reportSlot: vi.fn() };
  vi.stubGlobal("heinzelDesktop", { nativeFooter: bridge });
  expect(nativeFooter()).toMatchObject({ width: 180, bottom: 0 });
  bridge.metrics = () => ({ width: Number.NaN, bottom: 0 });
  expect(nativeFooter()).toBeNull();
  bridge.metrics = () => {
    throw new Error("unavailable");
  };
  expect(nativeFooter()).toBeNull();
});

it("reports the actual slot and re-reports it when the native document handshake changes", async () => {
  const reportSlot = vi.fn().mockResolvedValue(true);
  const bridge = { metrics: () => ({ width: 100, bottom: 34 }), reportSlot };
  vi.stubGlobal("heinzelDesktop", { nativeFooter: bridge });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 500, 100, 48));
  const metrics = nativeFooter();
  if (!metrics) throw new Error("missing fixture bridge");
  render(<NativeFooterSlot metrics={metrics} />);
  await waitFor(() => expect(reportSlot).toHaveBeenCalledTimes(1));
  expect(reportSlot).toHaveBeenLastCalledWith(expect.objectContaining({ x: 0, y: 500, width: 100, height: 48 }));
  act(() => window.dispatchEvent(new Event("heinzel-native-footer-metrics")));
  await waitFor(() => expect(reportSlot).toHaveBeenCalledTimes(2));
});

it("reports the full visual viewport, with native insets handled separately", async () => {
  const reportSlot = vi.fn().mockResolvedValue(true);
  const bridge = { metrics: () => ({ width: 100, bottom: 34 }), reportSlot };
  vi.stubGlobal("heinzelDesktop", { nativeFooter: bridge });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  const visual = Object.assign(new EventTarget(), { offsetLeft: 0, offsetTop: 0, width: 390, height: 844 });
  vi.stubGlobal("visualViewport", visual);
  document.documentElement.style.setProperty("--hz-viewport-safe-bottom", "34px");
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 762, 100, 48));
  const metrics = nativeFooter();
  if (!metrics) throw new Error("missing fixture bridge");
  render(<NativeFooterSlot metrics={metrics} />);
  await waitFor(() =>
    expect(reportSlot).toHaveBeenLastCalledWith(expect.objectContaining({ viewportWidth: 390, viewportHeight: 844 })),
  );
  visual.height = 600;
  act(() => visual.dispatchEvent(new Event("resize")));
  await waitFor(() =>
    expect(reportSlot).toHaveBeenLastCalledWith(expect.objectContaining({ viewportWidth: 390, viewportHeight: 600 })),
  );
});

it.each([false, "reject", "timeout"])("retries an unacknowledged native slot (%s)", async (failure) => {
  const reportSlot = vi.fn();
  if (failure === "reject") reportSlot.mockRejectedValueOnce(new Error("temporary"));
  else if (failure === "timeout") reportSlot.mockImplementationOnce(() => new Promise(() => {}));
  else reportSlot.mockResolvedValueOnce(false);
  reportSlot.mockResolvedValue(true);
  vi.stubGlobal("heinzelDesktop", { nativeFooter: { metrics: () => ({ width: 100, bottom: 34 }), reportSlot } });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 500, 100, 48));
  const metrics = nativeFooter();
  if (!metrics) throw new Error("missing fixture bridge");
  render(<NativeFooterSlot metrics={metrics} />);
  await waitFor(() => expect(reportSlot).toHaveBeenCalledTimes(2), { timeout: 2500 });
});

it("does not send a queued retry after the footer unmounts", async () => {
  vi.useFakeTimers();
  try {
    const reportSlot = vi.fn().mockResolvedValue(false);
    vi.stubGlobal("heinzelDesktop", { nativeFooter: { metrics: () => ({ width: 100, bottom: 34 }), reportSlot } });
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 500, 100, 48));
    const metrics = nativeFooter();
    if (!metrics) throw new Error("missing fixture bridge");
    const view = render(<NativeFooterSlot metrics={metrics} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(reportSlot).toHaveBeenCalledTimes(1);
    view.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(reportSlot).toHaveBeenCalledTimes(1);
  } finally {
    vi.useRealTimers();
  }
});
