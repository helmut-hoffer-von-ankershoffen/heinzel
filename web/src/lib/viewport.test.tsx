import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { clampToWindow, placePopover, popoverHeight } from "@/lib/placement";
import { useVisualViewport, viewportBounds, watchViewport } from "./viewport";

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("style");
});

function visible() {
  const viewport = Object.assign(new EventTarget(), {
    width: 300,
    height: 400,
    offsetLeft: 40,
    offsetTop: 120,
    scale: 1,
  });
  vi.stubGlobal("visualViewport", viewport);
  return viewport;
}

it("clamps popovers and floating players inside a panned visual viewport", () => {
  visible();
  const bounds = viewportBounds();
  expect(bounds).toEqual({ left: 40, top: 120, width: 300, height: 400 });
  expect(clampToWindow(-20, 900, { width: 100, height: 100 })).toEqual({ x: 40, y: 420 });
  expect(placePopover({ left: 0, right: 20, top: 10, bottom: 30 }, { width: 280, height: 300 }, "above")).toEqual({
    left: 48,
    top: 128,
  });
});

it("keeps popovers out of the device safe areas", () => {
  visible();
  const root = document.documentElement;
  root.style.setProperty("--hz-viewport-safe-left", "20px");
  root.style.setProperty("--hz-viewport-safe-right", "10px");
  root.style.setProperty("--hz-viewport-safe-top", "47px");
  root.style.setProperty("--hz-viewport-safe-bottom", "34px");
  expect(viewportBounds()).toEqual({ left: 60, top: 167, width: 270, height: 319 });
});

it("repositions on visual viewport resize and scroll and removes its listeners", () => {
  const viewport = visible();
  const update = vi.fn();
  const stop = watchViewport(update);
  viewport.dispatchEvent(new Event("resize"));
  viewport.dispatchEvent(new Event("scroll"));
  window.dispatchEvent(new Event("resize"));
  expect(update).toHaveBeenCalledTimes(3);
  stop();
  viewport.dispatchEvent(new Event("resize"));
  viewport.dispatchEvent(new Event("scroll"));
  window.dispatchEvent(new Event("resize"));
  expect(update).toHaveBeenCalledTimes(3);
});

it("updates dialog CSS bounds when the keyboard or viewport pan changes them", () => {
  const viewport = visible();
  const { unmount } = renderHook(useVisualViewport);
  const root = document.documentElement;
  expect(root.style.getPropertyValue("--hz-viewport-top")).toBe("120px");
  act(() => {
    viewport.height = 240;
    viewport.offsetTop = 200;
    viewport.dispatchEvent(new Event("resize"));
    viewport.dispatchEvent(new Event("scroll"));
  });
  expect(root.style.getPropertyValue("--hz-viewport-height")).toBe("240px");
  expect(root.style.getPropertyValue("--hz-viewport-top")).toBe("200px");
  expect(root.style.getPropertyValue("--hz-layout-height")).toBe("240px");
  act(() => {
    viewport.scale = 2;
    viewport.height = 120;
    viewport.dispatchEvent(new Event("resize"));
  });
  expect(root.style.getPropertyValue("--hz-layout-height")).toBe("240px");
  unmount();
  expect(root.style.getPropertyValue("--hz-viewport-height")).toBe("");
});

it("uses the layout viewport when the browser has no visual viewport API", () => {
  vi.stubGlobal("visualViewport", undefined);
  expect(viewportBounds()).toEqual({ left: 0, top: 0, width: innerWidth, height: innerHeight });
});

it("limits footer panes to the free space above their trigger", () => {
  visible();
  expect(popoverHeight({ left: 50, right: 80, top: 480, bottom: 504 })).toBe(346);
});

it("keeps a height-limited footer pane clear of its trigger even when the trigger leaves the viewport", () => {
  visible();
  const target = { left: 50, right: 80, top: 480, bottom: 504 };
  const height = popoverHeight(target);
  const at = placePopover(target, { width: 200, height }, "above");
  expect(at.top + height).toBeLessThan(target.top);
  expect(popoverHeight({ ...target, top: 800, bottom: 824 })).toBeLessThanOrEqual(384);
});
