import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { anchorWorkspace } from "./workspaceAnchor";

let resized: () => void;
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
  expect(stop).toHaveBeenCalledExactlyOnceWith({ top: 400, behavior: "instant" });
  view.page.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  expect(stop).toHaveBeenCalledTimes(1);
  view.dispose();
});
