import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { SidebarResizer } from "./SidebarResizer";

function Harness({ onFinal }: { onFinal?: (width: number) => void }) {
  const [width, setWidth] = useState(400);
  return (
    <SidebarResizer
      width={width}
      max={800}
      onChange={(next, final) => {
        const clamped = Math.min(800, Math.max(320, next));
        setWidth(clamped);
        if (final) onFinal?.(clamped);
      }}
    />
  );
}

describe("SidebarResizer", () => {
  beforeAll(() => {
    // jsdom has no pointer capture.
    HTMLElement.prototype.setPointerCapture = vi.fn();
    HTMLElement.prototype.releasePointerCapture = vi.fn();
    HTMLElement.prototype.hasPointerCapture = () => false;
  });

  it("is a vertical separator with value, bounds and a no-drag hit area", () => {
    render(<Harness />);
    const handle = screen.getByRole("separator", { name: "Resize sidebar" });
    expect(handle).toHaveAttribute("aria-orientation", "vertical");
    expect(handle).toHaveAttribute("aria-valuenow", "400");
    expect(handle).toHaveAttribute("aria-valuemin", "320");
    expect(handle).toHaveAttribute("aria-valuemax", "800");
    expect(handle.className).toContain("no-drag");
  });

  it("resizes by keys, Shift, Home and End, and resets on double click", async () => {
    const user = userEvent.setup();
    const final = vi.fn();
    render(<Harness onFinal={final} />);
    const handle = screen.getByRole("separator");
    handle.focus();
    await user.keyboard("{ArrowLeft}");
    expect(handle).toHaveAttribute("aria-valuenow", "416");
    await user.keyboard("{ArrowRight}{ArrowRight}");
    expect(handle).toHaveAttribute("aria-valuenow", "384");
    await user.keyboard("{Shift>}{ArrowLeft}{/Shift}");
    expect(handle).toHaveAttribute("aria-valuenow", "448");
    await user.keyboard("{Home}");
    expect(handle).toHaveAttribute("aria-valuenow", "320");
    await user.keyboard("{End}");
    expect(handle).toHaveAttribute("aria-valuenow", "800");
    await user.dblClick(handle);
    expect(handle).toHaveAttribute("aria-valuenow", "384");
    expect(final).toHaveBeenLastCalledWith(384);
  });

  it("drags with pointer capture, remembers on release and marks the page while dragging", () => {
    const final = vi.fn();
    render(<Harness onFinal={final} />);
    const handle = screen.getByRole("separator");
    handle.setPointerCapture = vi.fn();
    handle.releasePointerCapture = vi.fn();
    handle.hasPointerCapture = () => true;
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
    fireEvent.pointerDown(handle, { pointerId: 1, pointerType: "touch", clientX: 900, button: 0 });
    expect(handle.setPointerCapture).toHaveBeenCalledWith(1);
    expect(document.documentElement).toHaveAttribute("data-sidebar-resizing");
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: 800 });
    expect(handle).toHaveAttribute("aria-valuenow", "500");
    fireEvent.pointerUp(handle, { pointerId: 1, clientX: 800 });
    expect(document.documentElement).not.toHaveAttribute("data-sidebar-resizing");
    expect(final).toHaveBeenLastCalledWith(500);
    vi.unstubAllGlobals();
  });
});
