import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { type ReactNode, useEffect, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FooterItem } from "./FooterItem";
import { FOOTER_CREDIT, FOOTER_MORE, FooterCredit, FooterLayout, FooterMore, FooterPart } from "./FooterLayout";

function Counter({ mounted }: { mounted: () => void }) {
  const [count, setCount] = useState(0);
  useEffect(() => {
    mounted();
  }, [mounted]);
  return (
    <button type="button" data-state="ok" onClick={() => setCount((value) => value + 1)}>
      Daemon {count}
    </button>
  );
}

function Empty() {
  return null;
}

function measuredFooter(initialWidth: number, daemon?: ReactNode) {
  let width = initialWidth;
  const sizes: Record<string, number> = {
    version: 48,
    daemon: 24,
    update: 24,
    health: 24,
    clock: 80,
    [FOOTER_MORE]: 24,
  };
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    let value =
      this.tagName === "FOOTER"
        ? width
        : (sizes[this.getAttribute("data-footer-content") ?? this.getAttribute("data-footer-part") ?? ""] ?? 0);
    if (this.getAttribute("data-footer-content") === FOOTER_CREDIT) {
      value =
        this.querySelector<HTMLElement>("[data-footer-credit-text]")?.parentElement?.style.width === "0px" ? 0 : 160;
    }
    if (this.hasAttribute("data-footer-credit-text")) value = 160;
    if (this.hasAttribute("data-footer-content") && !this.childElementCount) value = 0;
    return new DOMRect(0, 0, value, 24);
  });
  const drivers = new Set<() => void>();
  const observed = new Set<Element>();
  class Observer implements ResizeObserver {
    private fire: () => void;
    constructor(callback: ResizeObserverCallback) {
      this.fire = () => callback([], this);
      drivers.add(this.fire);
    }
    observe(element: Element) {
      observed.add(element);
    }
    unobserve(element: Element) {
      observed.delete(element);
    }
    disconnect() {
      drivers.delete(this.fire);
    }
  }
  vi.stubGlobal("ResizeObserver", Observer);
  const view = render(
    <FooterLayout style={{ columnGap: 8, padding: 0, borderWidth: 0 }}>
      <FooterPart id="version" pinned>
        <button type="button">Version</button>
      </FooterPart>
      <FooterPart id="daemon" priority={30}>
        {daemon ?? (
          <button type="button" data-state="ok">
            Daemon
          </button>
        )}
      </FooterPart>
      <FooterPart id="update" priority={100}>
        <button type="button">Update</button>
      </FooterPart>
      <FooterPart id="health" priority={90}>
        <button type="button">Health</button>
      </FooterPart>
      <FooterPart id="clock" pinned>
        <button type="button">Clock</button>
      </FooterPart>
      <FooterPart id={FOOTER_CREDIT}>
        <FooterCredit>built with AI in Berlin</FooterCredit>
      </FooterPart>
      <FooterMore />
    </FooterLayout>,
  );
  return {
    ...view,
    observed,
    resize: async (next: number) => {
      width = next;
      await act(() => {
        for (const driver of drivers) driver();
      });
    },
  };
}
afterEach(() => vi.restoreAllMocks());

describe("measured footer controls", () => {
  it("keeps fitting controls even in a narrow parent and compacts credit before any icon", async () => {
    const { container, observed } = measuredFooter(375);
    await waitFor(() =>
      expect(container.querySelector<HTMLElement>("[data-footer-credit-text]")?.parentElement).toHaveStyle({
        width: 0,
      }),
    );
    for (const name of ["Version", "Daemon", "Update", "Health", "Clock"])
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "More status items" })).not.toBeInTheDocument();
    expect(observed.has(container.querySelector("[data-footer-credit-text]") as Element)).toBe(true);
    expect(screen.getAllByText("built with AI in Berlin")).toHaveLength(1);
  });

  it("keeps keyboard focus reachable when resize moves a control into More and restores it when space returns", async () => {
    const { resize } = measuredFooter(375);
    await waitFor(() => expect(screen.queryByRole("button", { name: "More status items" })).not.toBeInTheDocument());
    screen.getByRole("button", { name: "Daemon" }).focus();
    await resize(220);
    await waitFor(() => expect(screen.getByRole("button", { name: "More status items" })).toHaveFocus());
    expect(screen.queryByRole("button", { name: "Daemon" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Update" })).toBeInTheDocument();
    await resize(500);
    await waitFor(() => expect(screen.getByRole("button", { name: "Daemon" })).toHaveFocus());
    expect(screen.queryByRole("button", { name: "More status items" })).not.toBeInTheDocument();
  });

  it("promotes a hidden failure even when its dimensions do not change", async () => {
    const { container } = measuredFooter(220);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Daemon" })).not.toBeInTheDocument());
    const daemon = container.querySelector<HTMLElement>('[data-footer-part="daemon"] button');
    await act(() => {
      daemon?.setAttribute("data-state", "bad");
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "Daemon" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Update" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clock" })).toBeInTheDocument();
  });
});

describe("single-instance overflow", () => {
  it("parks unresolved parts without phantom gaps or a spurious More row", async () => {
    const { container } = measuredFooter(220, <Empty />);
    await waitFor(() =>
      expect(container.querySelector('[data-footer-part="daemon"]')).toHaveAttribute("data-footer-hidden", "empty"),
    );
    expect(screen.queryByRole("button", { name: "More status items" })).not.toBeInTheDocument();
  });

  it("closes a hidden detail and keeps its one live control usable inside the portaled More list", async () => {
    const action = vi.fn();
    const { resize } = measuredFooter(
      500,
      <FooterItem label="Daemon" state="ok" text="Daemon">
        <button type="button" onClick={action}>
          Inspect jobs
        </button>
      </FooterItem>,
    );
    const original = screen.getByRole("button", { name: "Daemon" });
    fireEvent.click(original);
    expect(await screen.findByRole("region", { name: "Daemon" })).toBeInTheDocument();
    await resize(220);
    await waitFor(() => expect(screen.queryByRole("region", { name: "Daemon" })).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "More status items" }));
    const popup = await screen.findByRole("region", { name: "More status items" });
    expect(within(popup).getByRole("button", { name: "Daemon" })).toBe(original);
    fireEvent.click(original);
    const details = await screen.findByRole("region", { name: "Daemon" });
    fireEvent.mouseDown(within(details).getByRole("button", { name: "Inspect jobs" }));
    fireEvent.click(within(details).getByRole("button", { name: "Inspect jobs" }));
    expect(action).toHaveBeenCalledOnce();
    expect(popup).toBeInTheDocument();
    fireEvent.keyDown(original, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("region", { name: "Daemon" })).not.toBeInTheDocument());
    expect(popup).toBeInTheDocument();
    fireEvent.keyDown(original, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("region", { name: "More status items" })).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "More status items" })).toHaveFocus();
  });

  it("moves only hidden controls into More without remounting or resetting their state", async () => {
    const mounted = vi.fn();
    const { resize } = measuredFooter(500, <Counter mounted={mounted} />);
    const original = screen.getByRole("button", { name: "Daemon 0" });
    fireEvent.click(original);
    await resize(220);
    const more = await screen.findByRole("button", { name: "More status items" });
    fireEvent.click(more);
    const popup = await screen.findByRole("region", { name: "More status items" });
    expect(within(popup).getByRole("button", { name: "Daemon 1" })).toBe(original);
    expect(within(popup).queryByRole("button", { name: "Update" })).not.toBeInTheDocument();
    expect(within(popup).queryByRole("button", { name: "Clock" })).not.toBeInTheDocument();
    fireEvent.click(original);
    fireEvent.keyDown(original, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("region", { name: "More status items" })).not.toBeInTheDocument());
    await resize(500);
    expect(await screen.findByRole("button", { name: "Daemon 2" })).toBe(original);
    expect(mounted).toHaveBeenCalledOnce();
    expect(document.body.textContent).not.toContain("🐻");
  });
});
