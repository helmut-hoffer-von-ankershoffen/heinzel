import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { anchorWorkspace } from "./workspaceAnchor";

let resized: () => void;
let frames: Map<number, FrameRequestCallback>;
let frameId: number;
function render() {
  const pending = [...frames.values()];
  frames.clear();
  for (const frame of pending) frame(16);
}
let mutated: () => void;
const disconnected = vi.fn();
const mutationDisconnected = vi.fn();
class Observer {
  constructor(callback: () => void) {
    resized = callback;
  }
  observe() {}
  disconnect = disconnected;
}

class Mutations {
  constructor(callback: () => void) {
    mutated = callback;
  }
  observe() {}
  disconnect = mutationDisconnected;
}

beforeEach(() => {
  mutated = () => {};
  frames = new Map();
  frameId = 0;
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.set(++frameId, callback);
    return frameId;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  vi.stubGlobal("ResizeObserver", Observer);
  vi.stubGlobal("MutationObserver", Mutations);
});
afterEach(() => {
  document.body.replaceChildren();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function layout() {
  const scroller = document.createElement("div");
  const page = document.createElement("main");
  const workspace = document.createElement("aside");
  const button = document.createElement("button");
  let height = 800;
  let viewportHeight = 600;
  let viewportWidth = 360;
  Object.defineProperty(page, "offsetHeight", { get: () => height });
  Object.defineProperty(workspace, "offsetTop", { get: () => height });
  Object.defineProperty(workspace, "offsetHeight", { value: 400 });
  Object.defineProperty(scroller, "scrollHeight", { value: 1400, configurable: true });
  Object.defineProperty(scroller, "clientHeight", { get: () => viewportHeight });
  Object.defineProperty(scroller, "clientWidth", { get: () => viewportWidth });
  workspace.append(button);
  scroller.append(page, workspace);
  document.body.append(scroller);
  scroller.scrollTop = 400;
  const dispose = anchorWorkspace(scroller, page, workspace);
  return {
    scroller,
    page,
    workspace,
    button,
    dispose,
    grow: (change: number) => (height += change),
    resize: (width: number, height: number) => {
      viewportWidth = width;
      viewportHeight = height;
    },
  };
}

it("keeps a pressed workspace control at the same visible position while the page grows", () => {
  const view = layout();
  view.button.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  view.grow(1200);
  resized();
  expect(view.scroller.scrollTop).toBe(1600);
  view.grow(-200);
  resized();
  expect(view.scroller.scrollTop).toBe(1400);
  view.dispose();
});

it("anchors a DOM update at the microtask checkpoint before pointer release and the resize notification", () => {
  const view = layout();
  view.button.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  view.grow(1200);
  mutated();
  expect(view.scroller.scrollTop).toBe(1600);
  view.button.dispatchEvent(new Event("pointerup", { bubbles: true }));
  resized();
  expect(view.scroller.scrollTop).toBe(1600);
  view.dispose();
});

it.each(["Page interaction", "scroll away", "removed control", "closed workspace", "disconnected", "disposed"])(
  "ignores queued page mutations after %s",
  (reason) => {
    const view = layout();
    view.button.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    if (reason === "Page interaction") view.page.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    if (reason === "scroll away") {
      view.scroller.scrollTop = 0;
      view.scroller.dispatchEvent(new Event("scroll"));
    }
    if (reason === "removed control") view.button.remove();
    if (reason === "closed workspace") view.workspace.hidden = true;
    if (reason === "disconnected") view.workspace.remove();
    if (reason === "disposed") view.dispose();
    const before = view.scroller.scrollTop;
    view.grow(1200);
    mutated();
    expect(view.scroller.scrollTop).toBe(before);
    if (reason !== "disposed") view.dispose();
    expect(disconnected).toHaveBeenCalledOnce();
    expect(mutationDisconnected).toHaveBeenCalledOnce();
  },
);

it("does not compensate twice when the browser already anchored the scroll", () => {
  const view = layout();
  view.button.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  view.grow(1200);
  view.scroller.scrollTop = 1600;
  view.scroller.dispatchEvent(new Event("scroll"));
  resized();
  expect(view.scroller.scrollTop).toBe(1600);
  view.dispose();
});

it.each([
  [360, 420],
  [320, 600],
])("preserves editor visibility scrolling when the viewport becomes %i by %i", (width, height) => {
  const view = layout();
  view.button.focus();
  view.resize(width, height);
  view.grow(-180);
  // The browser and keyboard handler already revealed the focused editor in the new geometry.
  view.scroller.scrollTop = 388;
  view.scroller.dispatchEvent(new Event("scroll"));
  resized();
  expect(view.scroller.scrollTop).toBe(388);
  // Subsequent content loading still anchors within the new viewport.
  view.grow(100);
  resized();
  expect(view.scroller.scrollTop).toBe(488);
  view.dispose();
});

it("follows keyboard focus and the user's subsequent scroll position", () => {
  const view = layout();
  view.button.focus();
  view.scroller.scrollTop = 450;
  view.scroller.dispatchEvent(new Event("scroll"));
  view.grow(1200);
  resized();
  expect(view.scroller.scrollTop).toBe(1650);
  view.dispose();
});

it("leaves Page readers in place and releases the workspace anchor when it leaves view", () => {
  const view = layout();
  view.grow(100);
  resized();
  expect(view.scroller.scrollTop).toBe(400);
  view.button.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  view.scroller.scrollTop = 0;
  view.scroller.dispatchEvent(new Event("scroll"));
  view.grow(1200);
  resized();
  expect(view.scroller.scrollTop).toBe(0);
  view.dispose();
});

it.each(["pointerdown", "wheel"])("stops anchoring after %s with Page or cleanup", (event) => {
  const view = layout();
  view.button.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  view.page.dispatchEvent(new Event(event, { bubbles: true }));
  view.grow(100);
  resized();
  expect(view.scroller.scrollTop).toBe(400);
  view.button.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  view.dispose();
  view.grow(1200);
  resized();
  expect(view.scroller.scrollTop).toBe(400);
  expect(disconnected).toHaveBeenCalledOnce();
});

it("stops an in-flight programmatic scroll when a pointer presses a workspace control", () => {
  const view = layout();
  const stop = vi.fn();
  view.scroller.scrollTo = stop;
  view.button.dispatchEvent(new Event("focusin", { bubbles: true }));
  expect(stop).not.toHaveBeenCalled();
  view.button.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  expect(stop).toHaveBeenCalledOnce();
  expect(stop).toHaveBeenLastCalledWith({ top: 400, left: 0, behavior: "instant" });
  render();
  expect(stop).toHaveBeenNthCalledWith(2, { top: 400, left: 0, behavior: "instant" });
  view.page.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  expect(stop).toHaveBeenCalledTimes(2);
  view.dispose();
});

it.each([0, 400, 123.5])("acknowledges one residual native compositor frame at offset %s", (offset) => {
  const view = layout();
  Object.defineProperty(view.scroller, "scrollHeight", { value: 1000 });
  view.scroller.scrollTop = offset;
  view.scroller.scrollLeft = 7.5;
  // Document-relative geometry stays fixed when the outer compositor scrolls.
  vi.spyOn(view.button, "getBoundingClientRect").mockImplementation(
    () =>
      ({
        top: offset + 20 - view.scroller.scrollTop,
        left: 50 - view.scroller.scrollLeft,
        right: 130 - view.scroller.scrollLeft,
        bottom: offset + 40 - view.scroller.scrollTop,
        width: 80,
        height: 20,
      }) as DOMRect,
  );
  const stop = vi.fn((options?: ScrollToOptions | number, y?: number) => {
    const request = typeof options === "number" ? { left: options, top: y } : (options ?? {});
    view.scroller.scrollTop = request.top ?? 0;
    view.scroller.scrollLeft = request.left ?? 0;
  });
  view.scroller.scrollTo = stop;
  view.button.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  // The abort has returned, but the compositor's already-computed delta reaches Blink next frame.
  view.scroller.scrollTop = Math.max(0, offset - 99);
  view.scroller.dispatchEvent(new Event("scroll"));
  render();
  expect(view.scroller.scrollTop).toBe(offset);
  expect(view.scroller.scrollLeft).toBe(7.5);
  expect(stop).toHaveBeenCalledTimes(2);
  render();
  expect(stop).toHaveBeenCalledTimes(2);
  view.scroller.scrollTop = 37;
  view.scroller.dispatchEvent(new Event("scroll"));
  render();
  expect(view.scroller.scrollTop).toBe(37);
  expect(frames.size).toBe(0);
  view.dispose();
});

it.each(["wheel", "keydown", "pointermove", "touchmove", "pointerup", "pointercancel", "touchend", "touchcancel"])(
  "leaves native %s navigation free before acknowledgment",
  (name) => {
    const view = layout();
    const stop = vi.fn();
    view.scroller.scrollTo = stop;
    view.button.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    const event = new Event(name, { bubbles: true, cancelable: true });
    expect(view.button.dispatchEvent(event)).toBe(true);
    expect(event.defaultPrevented).toBe(false);
    view.scroller.scrollTop = 37;
    render();
    expect(stop).toHaveBeenCalledOnce();
    expect(view.scroller.scrollTop).toBe(37);
    expect(frames.size).toBe(0);
    view.dispose();
  },
);

it.each(["input", "textarea", "select", "label", "editable"])("preserves native %s focus/reveal", (tag) => {
  const view = layout();
  const control = document.createElement(tag === "editable" ? "div" : tag);
  if (tag === "editable") control.setAttribute("contenteditable", "true");
  const child = document.createElement("span");
  control.append(child);
  view.workspace.append(control);
  const stop = vi.fn();
  view.scroller.scrollTo = stop;
  child.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  render();
  expect(stop).not.toHaveBeenCalled();
  view.dispose();
});

it.each(["secondary", "nonprimary"])("ignores %s pointer presses", (kind) => {
  const view = layout();
  const stop = vi.fn();
  view.scroller.scrollTo = stop;
  const event = new Event("pointerdown", { bubbles: true });
  Object.defineProperty(event, kind === "secondary" ? "button" : "isPrimary", {
    value: kind === "secondary" ? 2 : false,
  });
  view.button.dispatchEvent(event);
  render();
  expect(stop).not.toHaveBeenCalled();
  view.dispose();
});

it.each(["growth", "viewport", "removed", "replaced", "hidden", "focus", "dispose", "window resize", "new press"])(
  "abandons the pending acknowledgment after %s",
  (reason) => {
    const view = layout();
    const stop = vi.fn();
    view.scroller.scrollTo = stop;
    view.button.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    if (reason === "growth") view.grow(100);
    if (reason === "viewport") view.resize(360, 400);
    if (reason === "removed") view.button.remove();
    if (reason === "replaced") view.button.replaceWith(document.createElement("button"));
    if (reason === "hidden") view.workspace.hidden = true;
    if (reason === "focus") view.page.dispatchEvent(new Event("focusin", { bubbles: true }));
    if (reason === "dispose") view.dispose();
    if (reason === "window resize") window.dispatchEvent(new Event("resize"));
    if (reason === "new press") view.page.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    render();
    expect(stop).toHaveBeenCalledOnce();
    expect(frames.size).toBe(0);
    if (reason !== "dispose") view.dispose();
  },
);

it.each([
  { left: -1, top: 0, right: 79, bottom: 20 },
  { left: 0, top: -1, right: 80, bottom: 19 },
  { left: 300, top: 0, right: 380, bottom: 20 },
  { left: 0, top: 590, right: 80, bottom: 610 },
])("preserves native focus reveal for a clipped link %j", (rect) => {
  const view = layout();
  const link = document.createElement("a");
  link.href = "#target";
  view.button.replaceWith(link);
  const control = link;
  vi.spyOn(control, "getBoundingClientRect").mockReturnValue({ ...rect, width: 80, height: 20 } as DOMRect);
  const stop = vi.fn();
  view.scroller.scrollTo = stop;
  control.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  control.focus();
  view.scroller.scrollTop = 430;
  render();
  expect(stop).not.toHaveBeenCalled();
  expect(view.scroller.scrollTop).toBe(430);
  expect(frames.size).toBe(0);
  view.dispose();
});

it.each(["top", "left", "right", "bottom"])(
  "keeps fractional %s fringe presses stable without admitting larger clipping",
  (edge) => {
    for (const clipping of [0.25, 0.5, 0.75]) {
      const view = layout();
      const control = document.createElement("div");
      control.tabIndex = 0;
      view.button.replaceWith(control);
      vi.spyOn(view.scroller, "getBoundingClientRect").mockReturnValue({ top: 50, left: 50 } as DOMRect);
      const top = edge === "top" ? -clipping : edge === "bottom" ? 580 + clipping : 20;
      const left = edge === "left" ? -clipping : edge === "right" ? 280 + clipping : 20;
      vi.spyOn(control, "getBoundingClientRect").mockImplementation(
        () =>
          ({
            top: top + 450 - view.scroller.scrollTop,
            bottom: top + 470 - view.scroller.scrollTop,
            left: left + 50,
            right: left + 130,
            width: 80,
            height: 20,
          }) as DOMRect,
      );
      const stop = vi.fn((options?: ScrollToOptions | number, y?: number) => {
        const request = typeof options === "number" ? { left: options, top: y } : (options ?? {});
        view.scroller.scrollTop = request.top ?? 0;
      });
      view.scroller.scrollTo = stop;
      control.dispatchEvent(new Event("pointerdown", { bubbles: true }));
      control.focus();
      // The original native compositor commits after the synchronous pointerdown abort.
      view.scroller.scrollTop = 300;
      view.scroller.dispatchEvent(new Event("scroll"));
      render();
      expect(stop).toHaveBeenCalledTimes(clipping <= 0.5 ? 2 : 0);
      expect(view.scroller.scrollTop).toBe(clipping <= 0.5 ? 400 : 300);
      expect(frames.size).toBe(0);
      view.dispose();
    }
  },
);

it.each([0.25, 0.5, 10])("leaves a control clipped %s pixels by the visual viewport to native focus", (clipping) => {
  const viewport = Object.assign(new EventTarget(), { width: 360, height: 400, offsetLeft: 0, offsetTop: 100 });
  vi.stubGlobal("visualViewport", viewport);
  const view = layout();
  const control = document.createElement("a");
  control.href = "#target";
  view.button.replaceWith(control);
  vi.spyOn(control, "getBoundingClientRect").mockReturnValue({
    left: 20,
    top: 100 - clipping,
    right: 100,
    bottom: 120 - clipping,
    width: 80,
    height: 20,
  } as DOMRect);
  const stop = vi.fn();
  view.scroller.scrollTo = stop;
  control.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  render();
  expect(stop).not.toHaveBeenCalled();
  expect(frames.size).toBe(0);
  view.dispose();
});

it.each(["resize", "scroll"])("releases the acknowledgment on visual viewport %s", (name) => {
  const viewport = new EventTarget();
  vi.stubGlobal("visualViewport", viewport);
  const view = layout();
  const stop = vi.fn();
  view.scroller.scrollTo = stop;
  const added = vi.spyOn(viewport, "addEventListener");
  const removed = vi.spyOn(viewport, "removeEventListener");
  view.button.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  viewport.dispatchEvent(new Event(name));
  render();
  expect(stop).toHaveBeenCalledOnce();
  expect(frames.size).toBe(0);
  for (const [event, listener] of added.mock.calls) expect(removed).toHaveBeenCalledWith(event, listener);
  view.dispose();
});

it("allows the pressed control's ordinary native focus and removes temporary listeners", () => {
  const view = layout();
  const stop = vi.fn();
  view.scroller.scrollTo = stop;
  const added = vi.spyOn(document, "addEventListener");
  const removed = vi.spyOn(document, "removeEventListener");
  view.button.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  view.button.focus();
  render();
  expect(stop).toHaveBeenCalledTimes(2);
  for (const [name, listener] of added.mock.calls) expect(removed).toHaveBeenCalledWith(name, listener, true);
  view.dispose();
});

it("does not manufacture scroll range or move a nested scroller", () => {
  const view = layout();
  Object.defineProperty(view.scroller, "scrollHeight", { value: 600 });
  const stop = vi.fn();
  view.scroller.scrollTo = stop;
  const nested = document.createElement("div");
  nested.scrollTop = 21;
  nested.append(view.button);
  view.workspace.append(nested);
  view.button.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  expect(stop).not.toHaveBeenCalled();
  expect(nested.scrollTop).toBe(21);
  view.dispose();
});

it.each(["button", "summary"])("stabilizes a pressed native %s through its visible clipped portion", (tag) => {
  for (const edge of ["top", "bottom", "left", "right"]) {
    for (const clipping of [0.25, 1.25, 5, 19]) {
      const view = layout();
      const control = document.createElement(tag);
      if (tag === "summary") {
        const details = document.createElement("details");
        details.append(control);
        view.button.replaceWith(details);
      } else view.button.replaceWith(control);
      const child = document.createElement("span");
      control.append(child);
      const top = edge === "top" ? -clipping : edge === "bottom" ? 580 + clipping : 20;
      const left = edge === "left" ? -clipping : edge === "right" ? 280 + clipping : 20;
      vi.spyOn(control, "getBoundingClientRect").mockImplementation(
        () =>
          ({
            top: top + 400 - view.scroller.scrollTop,
            bottom: top + 420 - view.scroller.scrollTop,
            left,
            right: left + 80,
            width: 80,
            height: 20,
          }) as DOMRect,
      );
      const stop = vi.fn((options?: ScrollToOptions | number, y?: number) => {
        const request = typeof options === "number" ? { left: options, top: y } : (options ?? {});
        view.scroller.scrollTop = request.top ?? 0;
      });
      view.scroller.scrollTo = stop;
      child.dispatchEvent(new Event("pointerdown", { bubbles: true }));
      control.focus();
      view.scroller.scrollTop = 300;
      render();
      expect(view.scroller.scrollTop).toBe(400);
      expect(stop).toHaveBeenCalledTimes(2);
      render();
      expect(stop).toHaveBeenCalledTimes(2);
      view.dispose();
    }
  }
});

it.each(["label", "focus transfer"])("keeps native button %s reveal authoritative", (kind) => {
  const view = layout();
  const input = document.createElement("input");
  view.workspace.append(input);
  if (kind === "label") {
    const label = document.createElement("label");
    view.button.replaceWith(label);
    label.append(view.button);
  }
  const stop = vi.fn();
  view.scroller.scrollTo = stop;
  view.button.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  input.focus();
  view.scroller.scrollTop = 37;
  render();
  expect(stop).toHaveBeenCalledTimes(kind === "label" ? 0 : 1);
  expect(view.scroller.scrollTop).toBe(37);
  expect(frames.size).toBe(0);
  view.dispose();
});
