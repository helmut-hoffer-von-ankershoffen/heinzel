import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ReactNode, useEffect, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FooterItem } from "./FooterItem";
import { FOOTER_MORE, FOOTER_RELOCATED, FooterLayout, FooterMore, FooterPart } from "./FooterLayout";

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

function Asked() {
  const [request, setRequest] = useState(0);
  useEffect(() => {
    const ask = () => setRequest((count) => count + 1);
    window.addEventListener("test:ask", ask);
    return () => window.removeEventListener("test:ask", ask);
  }, []);
  return (
    <FooterItem label="Updates" state="warn" text="Updates" openRequest={request}>
      <button type="button">Check for updates</button>
    </FooterItem>
  );
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
      <FooterMore />
    </FooterLayout>,
  );
  return {
    ...view,
    observed,
    relocate: (next: number) => {
      width = next;
      act(() => window.dispatchEvent(new Event(FOOTER_RELOCATED)));
    },
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
  it("keeps newly mounted controls inert until their first capacity measurement commits", () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => frames.push(callback));
    const { container } = measuredFooter(220);
    const daemon = container.querySelector('[data-footer-part="daemon"]');
    expect(daemon).toHaveAttribute("data-footer-hidden", "measuring");
    expect(daemon).toHaveAttribute("inert");
    expect(screen.queryByRole("button", { name: "Daemon" })).not.toBeInTheDocument();
    act(() => {
      for (const callback of frames.splice(0)) callback(0);
    });
    expect(daemon).toHaveAttribute("data-footer-hidden", "overflow");
    expect(screen.getByRole("button", { name: "More status items" })).toBeInTheDocument();
  });

  it("keeps fitting controls in a narrow parent without an attribution item", async () => {
    const { container, observed } = measuredFooter(375);
    await screen.findByRole("button", { name: "Version" });
    await waitFor(() => expect(screen.queryByRole("button", { name: "More status items" })).not.toBeInTheDocument());
    for (const name of ["Version", "Daemon", "Update", "Health", "Clock"])
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "More status items" })).not.toBeInTheDocument();
    expect(container.querySelector("[data-footer-credit-text]")).toBeNull();
    expect(observed.size).toBeGreaterThan(0);
  });

  it("updates overflow immediately when a retained footer is revealed in another host", async () => {
    const { relocate } = measuredFooter(220);
    await screen.findByRole("button", { name: "More status items" });
    relocate(500);
    expect(screen.queryByRole("button", { name: "More status items" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Daemon" })).toBeInTheDocument();
    relocate(220);
    expect(screen.getByRole("button", { name: "More status items" })).toBeInTheDocument();
  });

  it("keeps keyboard focus reachable when resize moves a control into More and restores it when space returns", async () => {
    const { resize } = measuredFooter(375);
    await screen.findByRole("button", { name: "Version" });
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
  it("opens More and pins a pane the owner asks for while it is hidden", async () => {
    measuredFooter(220, <Asked />);
    await screen.findByRole("button", { name: "More status items" });
    expect(screen.queryByRole("button", { name: "Check for updates" })).not.toBeInTheDocument();
    act(() => {
      window.dispatchEvent(new Event("test:ask"));
    });
    expect(await screen.findByRole("button", { name: "Check for updates" })).toBeInTheDocument();
  });

  it("anchors More above its trigger independently of asynchronously growing rows", async () => {
    measuredFooter(220);
    const more = await screen.findByRole("button", { name: "More status items" });
    const original = vi.mocked(Element.prototype.getBoundingClientRect).getMockImplementation();
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
      if (this === more) return new DOMRect(200, 700, 24, 24);
      if (this.matches('section[aria-label="More status items"]'))
        return new DOMRect(0, 0, 288, 18 + this.childElementCount * 56);
      return original?.call(this) ?? new DOMRect();
    });
    fireEvent.click(more);
    const popup = screen.getByRole("region", { name: "More status items" });
    expect(popup.childElementCount).toBeGreaterThan(0);
    expect(popup.style.top).toBe("694px");
    expect(popup).toHaveClass("-translate-y-full");
  });

  it("focuses the More list without opening a detail over its other controls", async () => {
    const user = userEvent.setup();
    measuredFooter(
      220,
      <FooterItem label="Daemon" state="ok" text="Daemon">
        <button type="button">Inspect jobs</button>
      </FooterItem>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "More status items" }));
    const popup = await screen.findByRole("region", { name: "More status items" });
    await waitFor(() => expect(popup).toHaveFocus());
    const trigger = within(popup).getByRole("button", { name: "Daemon" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("region", { name: "Daemon" })).not.toBeInTheDocument();
    await user.tab();
    expect(trigger).toHaveFocus();
    expect(await screen.findByRole("region", { name: "Daemon" })).toBeInTheDocument();
  });

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
    const original = await screen.findByRole("button", { name: "Daemon" });
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
    const original = await screen.findByRole("button", { name: "Daemon 0" });
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
