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
