import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { AnchoredPopover } from "./AnchoredPopover";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function Example() {
  const anchor = useRef<HTMLButtonElement>(null);
  const [open, show] = useState(false);
  return (
    <>
      <button ref={anchor} type="button" onClick={() => show(true)}>
        Choose
      </button>
      {open && (
        <AnchoredPopover anchor={anchor} role="dialog" aria-label="Choices" focusFirst>
          <input aria-label="Find a choice" />
        </AnchoredPopover>
      )}
    </>
  );
}

it("fits a dropdown above an edge trigger and focuses its input only after placement", async () => {
  vi.stubGlobal(
    "visualViewport",
    Object.assign(new EventTarget(), { width: 360, height: 640, offsetLeft: 0, offsetTop: 0 }),
  );
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    return this.tagName === "BUTTON" ? new DOMRect(320, 600, 24, 24) : new DOMRect(0, 0, 288, 150);
  });
  const user = userEvent.setup();
  render(<Example />);
  await user.click(screen.getByRole("button", { name: "Choose" }));
  expect(screen.getByRole("dialog", { name: "Choices" })).toHaveStyle({ left: "64px", top: "444px" });
  expect(screen.getByRole("textbox", { name: "Find a choice" })).toHaveFocus();
});

function LateFacts() {
  const [loaded, setLoaded] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setLoaded(true)}>
        Load details
      </button>
      {loaded && <p data-late-facts>Additional runtime details</p>}
      <button type="button">Run action</button>
    </>
  );
}
function MutationExample({ onRender }: { onRender: () => void }) {
  onRender();
  const anchor = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button ref={anchor} type="button">
        Anchor
      </button>
      <AnchoredPopover anchor={anchor} side="above" role="dialog" aria-label="Runtime facts">
        <LateFacts />
      </AnchoredPopover>
    </>
  );
}

it("keeps a bottom action fixed at the first child mutation delivery before resize observation", async () => {
  vi.stubGlobal(
    "visualViewport",
    Object.assign(new EventTarget(), { width: 360, height: 640, offsetLeft: 0, offsetTop: 0 }),
  );
  const resize = vi.fn();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe = resize;
      disconnect = vi.fn();
    },
  );
  const parentRender = vi.fn();
  const measuredPane = vi.fn();
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    if (this.textContent === "Anchor") return new DOMRect(320, 600, 24, 24);
    const dialog = this.closest('[role="dialog"]') as HTMLElement | null;
    if (dialog) {
      const height = dialog.querySelector("[data-late-facts]") ? 194 : 150;
      const top = Number.parseFloat(dialog.style.top) || 0;
      if (this === dialog) {
        measuredPane();
        return new DOMRect(0, top, 288, height);
      }
      return new DOMRect(0, top + height - 30, 100, 24);
    }
    return new DOMRect();
  });
  render(<MutationExample onRender={parentRender} />);
  const action = screen.getByRole("button", { name: "Run action" });
  const before = action.getBoundingClientRect().y;
  const delivery = new Promise<number>((resolve) => {
    const observer = new MutationObserver(() => {
      if (!document.querySelector("[data-late-facts]")) return;
      observer.disconnect();
      resolve(action.getBoundingClientRect().y);
    });
    observer.observe(document.body, { subtree: true, childList: true, characterData: true });
  });
  await userEvent.setup().click(screen.getByRole("button", { name: "Load details" }));
  expect(await delivery).toBe(before);
  expect(screen.getByRole("dialog", { name: "Runtime facts" })).toHaveStyle({ top: "400px" });
  expect(parentRender).toHaveBeenCalledTimes(1);
  expect(measuredPane).toHaveBeenCalledTimes(2);
  expect(resize).toHaveBeenCalled();
});
