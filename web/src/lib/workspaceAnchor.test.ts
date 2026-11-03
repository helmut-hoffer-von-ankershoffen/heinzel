import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { anchorWorkspace } from "./workspaceAnchor";

let resized: () => void;
const disconnected = vi.fn();
class Observer {
  constructor(callback: () => void) {
    resized = callback;
  }
  observe() {}
  disconnect = disconnected;
}

beforeEach(() => vi.stubGlobal("ResizeObserver", Observer));
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
  Object.defineProperty(page, "offsetHeight", { get: () => height });
  Object.defineProperty(workspace, "offsetTop", { get: () => height });
  Object.defineProperty(workspace, "offsetHeight", { value: 400 });
  Object.defineProperty(scroller, "clientHeight", { value: 600 });
  workspace.append(button);
  scroller.append(page, workspace);
  document.body.append(scroller);
  scroller.scrollTop = 400;
  const dispose = anchorWorkspace(scroller, page, workspace);
  return { scroller, page, button, dispose, grow: (change: number) => (height += change) };
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
