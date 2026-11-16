import { render, waitFor } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { type SurfaceBridge, useNativeSurface } from "./nativeSurface";

function Surface({ send }: { send: SurfaceBridge }) {
  const ref = useRef<HTMLDivElement>(null);
  useNativeSurface(ref, true, send);
  return <div ref={ref} data-testid="surface" />;
}

afterEach(() => vi.restoreAllMocks());

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
