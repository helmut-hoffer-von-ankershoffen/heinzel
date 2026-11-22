import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { anchorPress } from "./pressAnchor";

let mutated: () => void;
let resized: () => void;
const disconnected = vi.fn();
class Mutations {
  constructor(callback: () => void) {
    mutated = callback;
  }
  observe() {}
  disconnect = disconnected;
}
class Resizes {
  constructor(callback: () => void) {
    resized = callback;
  }
  observe() {}
  disconnect = disconnected;
}

beforeEach(() => {
  vi.stubGlobal("MutationObserver", Mutations);
  vi.stubGlobal("ResizeObserver", Resizes);
});
afterEach(() => {
  document.body.replaceChildren();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

// The button's document position is fixed by `above`; its visible top is that minus the scroll.
function layout() {
  const scroller = document.createElement("div");
  const page = document.createElement("main");
  const button = document.createElement("button");
  const other = document.createElement("aside");
  const outside = document.createElement("button");
  let above = 600;
  let viewport = 700;
  Object.defineProperty(scroller, "clientHeight", { get: () => viewport });
  Object.defineProperty(scroller, "clientWidth", { value: 390 });
  button.getBoundingClientRect = () => ({ top: above - scroller.scrollTop }) as DOMRect;
  page.append(button);
  other.append(outside);
  scroller.append(page, other);
  document.body.append(scroller);
  scroller.scrollTop = 400;
  const dispose = anchorPress(scroller, page);
  return {
    scroller,
    button,
    outside,
    dispose,
    grow: (change: number) => (above += change),
    reflow: (height: number) => (viewport = height),
    press: (target: Element, init: PointerEventInit = { button: 0, isPrimary: true }) =>
      target.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, ...init })),
  };
}

it("keeps a pressed page button under the pointer when content above it loads", () => {
  const view = layout();
  view.press(view.button);
  view.grow(184);
  mutated();
  expect(view.scroller.scrollTop).toBe(584);
  view.grow(-84);
  resized();
  expect(view.scroller.scrollTop).toBe(500);
  view.dispose();
  expect(disconnected).toHaveBeenCalledTimes(2);
});

it("leaves no shift when the browser already anchored the press", () => {
  const view = layout();
  view.press(view.button);
  view.grow(184);
  view.scroller.scrollTop = 584;
  view.scroller.dispatchEvent(new Event("scroll"));
  mutated();
  expect(view.scroller.scrollTop).toBe(584);
  view.dispose();
});

it.each(["pointerup", "pointercancel", "touchmove", "wheel", "keydown"])("ends the press on %s", (name) => {
  const view = layout();
  view.press(view.button);
  view.button.dispatchEvent(new Event(name, { bubbles: true }));
  view.grow(184);
  mutated();
  expect(view.scroller.scrollTop).toBe(400);
  view.dispose();
});

it.each([
  ["a secondary button", { button: 2, isPrimary: true }],
  ["a second finger", { button: 0, isPrimary: false }],
])("ignores %s", (_, init) => {
  const view = layout();
  view.press(view.button, init);
  view.grow(184);
  mutated();
  expect(view.scroller.scrollTop).toBe(400);
  view.dispose();
});

it.each(["outside the region", "removed control", "viewport reflow", "disposed"])("does not anchor: %s", (reason) => {
  const view = layout();
  view.press(reason === "outside the region" ? view.outside : view.button);
  if (reason === "removed control") view.button.remove();
  if (reason === "viewport reflow") view.reflow(400);
  if (reason === "disposed") view.dispose();
  view.grow(184);
  mutated();
  expect(view.scroller.scrollTop).toBe(400);
  if (reason !== "disposed") view.dispose();
});
