import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { expect, it, vi } from "vitest";

it("coalesces trusted clicks until the native handoff finishes", async () => {
  const source = ["WorkspaceView", "NativePrompt", "TitleBarDragLayout", "Navigation"]
    .map((name) => readFileSync(`../apple/App/${name}.swift`, "utf8"))
    .join("\n");
  const start = source.indexOf("    let guestOpenPending = false;");
  const end = source.indexOf('    addEventListener("pointerdown", activate, true);', start);
  expect(start).toBeGreaterThanOrEqual(0);
  expect(end).toBeGreaterThan(start);
  let finish!: (result: boolean) => void;
  const pending = new Promise<boolean>((resolve) => {
    finish = resolve;
  });
  const postMessage = vi.fn(() => pending);
  const dispatchEvent = vi.fn((_event: { type: string }) => undefined);
  class Element {
    closest() {
      return this;
    }
  }
  class HTMLButtonElement extends Element {
    disabled = false;
  }
  class Event {
    constructor(public type: string) {}
  }
  const context = {
    Element,
    HTMLButtonElement,
    Event,
    window: { dispatchEvent, webkit: { messageHandlers: { heinzelExternalLink: { postMessage } } } },
  };
  const openGuest = runInNewContext(`${source.slice(start, end)}\nopenGuest`, context) as (event: unknown) => void;
  const click = (isTrusted = true) => ({
    isTrusted,
    button: 0,
    defaultPrevented: false,
    target: new HTMLButtonElement(),
    preventDefault: vi.fn(),
    stopImmediatePropagation: vi.fn(),
  });
  openGuest(click(false));
  expect(postMessage).not.toHaveBeenCalled();
  const first = click();
  const duplicate = click();
  openGuest(first);
  openGuest(duplicate);
  expect(postMessage).toHaveBeenCalledTimes(1);
  expect(duplicate.stopImmediatePropagation).toHaveBeenCalledOnce();
  expect(dispatchEvent.mock.calls.map(([event]) => event.type)).toEqual(["heinzel-guest-external-started"]);
  finish(true);
  await pending;
  await Promise.resolve();
  expect(dispatchEvent.mock.calls.map(([event]) => event.type)).toEqual(["heinzel-guest-external-started"]);
  openGuest(click());
  expect(postMessage).toHaveBeenCalledTimes(2);
});
