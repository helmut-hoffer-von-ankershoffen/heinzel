import { act, render, screen, waitFor } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { type SurfaceBridge, useNativeSurface } from "./nativeSurface";
import { loadedFrontend, recordSurface, surfaceDetails } from "./surfaceDiagnostics";

function Surface({ send, cutout = false }: { send: SurfaceBridge; cutout?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const failed = useNativeSurface(ref, true, send, undefined, "browser", cutout);
  return (
    <>
      <div ref={ref} data-testid="surface" />
      {failed && <p role="alert">Display failed</p>}
    </>
  );
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it("shows a surface inside its player card and hides it only for an intersecting overlay", async () => {
  const box = { x: 10, y: 10, left: 10, top: 10, right: 110, bottom: 110, width: 100, height: 100, toJSON: () => ({}) };
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(box);
  const send = vi.fn(async () => undefined);
  const card = document.createElement("section");
  card.className = "viewport-popover";
  card.setAttribute("data-native-surface-container", "");
  Object.defineProperty(card, "getClientRects", { value: () => [box] });
  document.body.append(card);
  const view = render(<Surface send={send} />, { container: card });
  try {
    await waitFor(() => expect(send).toHaveBeenCalledWith(expect.objectContaining({ action: "layout" })));
    send.mockClear();
    const other = document.createElement("div");
    other.className = "viewport-popover";
    other.setAttribute("data-native-surface-container", "");
    Object.defineProperty(other, "getClientRects", {
      configurable: true,
      value: () => [{ left: 200, right: 300, top: 10, bottom: 110 }],
    });
    document.body.append(other);
    await waitFor(() => expect(send).toHaveBeenCalledWith(expect.objectContaining({ action: "layout" })));
    expect(send).not.toHaveBeenCalledWith({ action: "hide" });
    send.mockClear();
    Object.defineProperty(other, "getClientRects", { configurable: true, value: () => [box] });
    other.classList.add("overlapping");
    await waitFor(() => expect(send).toHaveBeenCalledWith({ action: "hide" }));
    other.remove();
  } finally {
    view.unmount();
    card.remove();
  }
});

it("clips only the floating player footprint on capable hosts and hides for trusted overlays", async () => {
  const box = new DOMRect(10, 10, 100, 100);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(box);
  const card = document.createElement("section");
  card.className = "viewport-popover";
  card.dataset.nativeSurfaceContainer = "";
  card.dataset.dock = "floating";
  Object.defineProperty(card, "getClientRects", { value: () => [new DOMRect(80, 83, 70, 70)] });
  const dialog = document.createElement("dialog");
  Object.defineProperty(dialog, "getClientRects", { value: () => [box] });
  const send = vi.fn(async () => undefined);
  document.body.append(card);
  const view = render(<Surface send={send} cutout />);
  try {
    await waitFor(() =>
      expect(send).toHaveBeenLastCalledWith({
        action: "layout",
        bounds: { x: 10, y: 10, width: 100, height: 100 },
        playerCutout: { x: 80, y: 83, width: 30, height: 27 },
      }),
    );
    dialog.open = true;
    document.body.append(dialog);
    await waitFor(() => expect(send).toHaveBeenLastCalledWith({ action: "hide" }));
    dialog.remove();
    card.remove();
    await waitFor(() =>
      expect(send).toHaveBeenLastCalledWith({
        action: "layout",
        bounds: { x: 10, y: 10, width: 100, height: 100 },
      }),
    );
  } finally {
    dialog.remove();
    card.remove();
    view.unmount();
  }
});

it("records an overlay decision and surfaces bridge rejection without exception text", async () => {
  const box = { x: 10, y: 10, left: 10, top: 10, right: 110, bottom: 110, width: 100, height: 100, toJSON: () => ({}) };
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(box);
  const send = vi.fn(async (request: Record<string, unknown>) => {
    if (request.action === "hide") throw new Error("private data");
  });
  const view = render(<Surface send={send} />);
  const overlay = document.createElement("div");
  overlay.setAttribute("role", "dialog");
  Object.defineProperty(overlay, "getClientRects", { value: () => [box] });
  document.body.append(overlay);
  try {
    expect(await screen.findByRole("alert")).toHaveTextContent("Display failed");
    expect(surfaceDetails().join(" ")).toContain("overlay");
    expect(surfaceDetails().join(" ")).not.toContain("private data");
  } finally {
    overlay.remove();
    view.unmount();
  }
});

it("surfaces a rejected layout without exposing its error", async () => {
  const box = { x: 10, y: 10, left: 10, top: 10, right: 110, bottom: 110, width: 100, height: 100, toJSON: () => ({}) };
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(box);
  const send = vi.fn(async (request: Record<string, unknown>) => {
    if (request.action === "layout") throw new Error("secret");
  });
  render(<Surface send={send} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Display failed");
  expect(surfaceDetails().join(" ")).toContain("layout requested; bridge: failed");
  expect(surfaceDetails().join(" ")).not.toContain("secret");
});

it("keeps an out-of-viewport guest hidden and records why", async () => {
  const box = { x: -2, y: 10, left: -2, top: 10, right: 98, bottom: 110, width: 100, height: 100, toJSON: () => ({}) };
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(box);
  const send = vi.fn(async () => undefined);
  render(<Surface send={send} />);
  await waitFor(() => expect(send).toHaveBeenCalledWith({ action: "hide" }));
  expect(surfaceDetails().join(" ")).toContain("outside viewport");
  expect(send).not.toHaveBeenCalledWith(expect.objectContaining({ action: "layout" }));
});

it("reports only a local hashed frontend asset, without URL data", () => {
  const page = document.implementation.createHTMLDocument();
  page.body.innerHTML = '<script type="module" src="/assets/index-123abc.js"></script>';
  expect(loadedFrontend(page)).toBe("/assets/index-123abc.js");
  page.body.innerHTML = '<script type="module" src="https://private.example/assets/a.js?token=secret"></script>';
  expect(loadedFrontend(page)).toBeNull();
});

it("clears a native sample on teardown and ignores a late reply", async () => {
  vi.useFakeTimers();
  let resolveSample!: (value: unknown) => void;
  const sample = new Promise((resolve) => {
    resolveSample = resolve;
  });
  const send = vi.fn(async (request: Record<string, unknown>) =>
    request.action === "diagnostics" ? sample : undefined,
  );
  recordSurface("browser", "layout requested", "ok", { focused: true, visible: true, status: "ready" });
  const view = render(<Surface send={send} />);
  act(() => vi.advanceTimersByTime(500));
  view.unmount();
  await act(async () => {
    resolveSample({ focused: true, visible: true, status: "ready" });
    await Promise.resolve();
  });
  expect(surfaceDetails().join(" ")).toContain("browser surface: inactive");
  expect(surfaceDetails().join(" ")).toContain("native last sample: unavailable");
});

it("keeps a newer native sample when an older request finishes later", async () => {
  vi.useFakeTimers();
  let resolveOld!: (value: unknown) => void;
  const old = new Promise((resolve) => {
    resolveOld = resolve;
  });
  let count = 0;
  const send = vi.fn(async (request: Record<string, unknown>) => {
    if (request.action !== "diagnostics") return undefined;
    count += 1;
    return count === 1 ? old : { focused: false, visible: false, status: "ready" };
  });
  const view = render(<Surface send={send} />);
  await act(async () => {
    vi.advanceTimersByTime(1000);
    await Promise.resolve();
  });
  expect(surfaceDetails().join(" ")).toContain("focused=false view-visible=false");
  await act(async () => {
    resolveOld({ focused: true, visible: true, status: "ready" });
    await Promise.resolve();
  });
  expect(surfaceDetails().join(" ")).toContain("focused=false view-visible=false");
  view.unmount();
});
