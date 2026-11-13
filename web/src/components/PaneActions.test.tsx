import { act, render, screen } from "@testing-library/react";
import {
  BellRing,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Copy,
  ExternalLink,
  Plug,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { actionStructureProblems, PaneAction, PaneActions, PaneTitle } from "./PaneActions";

describe("PaneActions", () => {
  it("leads with the first action's words and draws the rest as named icons, destructive red", () => {
    const { container } = render(
      <MemoryRouter>
        <PaneActions>
          <PaneAction icon={RefreshCw} to="/doctor">
            Open Doctor
          </PaneAction>
          <PaneAction icon={Copy} onClick={() => {}}>
            Copy address
          </PaneAction>
          <PaneAction icon={Trash2} destructive onClick={() => {}}>
            Remove
          </PaneAction>
        </PaneActions>
      </MemoryRouter>,
    );
    const row = screen.getByRole("group", { name: "Actions" });
    expect(row).toBe(container.querySelector("[data-pane-actions]"));
    expect(row).toHaveClass("border-t", "flex", "flex-nowrap");
    expect(row).toHaveAttribute("data-words", "lead");
    const doctor = screen.getByRole("link", { name: "Open Doctor" });
    expect(doctor).toHaveAttribute("href", "/doctor");
    expect(doctor).toHaveTextContent("Open Doctor");
    expect(doctor).not.toHaveAttribute("title");
    const copy = screen.getByRole("button", { name: "Copy address" });
    expect(copy).toHaveAttribute("title", "Copy address");
    expect(copy).not.toHaveTextContent("Copy address");
    const remove = screen.getByRole("button", { name: "Remove" });
    expect(remove).toHaveClass("text-bad");
    expect(remove).toHaveAttribute("title", "Remove");
    expect(actionStructureProblems(container)).toEqual([]);
  });

  it("drops the lead to its icon when its words would overflow, without a second row", () => {
    const widths = vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockReturnValue(400);
    const client = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(250);
    try {
      render(
        <PaneActions>
          <PaneAction icon={RefreshCw} onClick={() => {}}>
            Restart daemon
          </PaneAction>
          <PaneAction icon={Copy} onClick={() => {}}>
            Logs
          </PaneAction>
        </PaneActions>,
      );
      const row = screen.getByRole("group", { name: "Actions" });
      expect(row).toHaveAttribute("data-words", "none");
      const lead = screen.getByRole("button", { name: "Restart daemon" });
      expect(lead).toHaveAttribute("title", "Restart daemon");
      expect(lead).not.toHaveTextContent("Restart daemon");
      expect(actionStructureProblems(row.parentElement as HTMLElement)).toEqual([]);
    } finally {
      widths.mockRestore();
      client.mockRestore();
    }
  });

  it("puts a lead the row has no room for on its own line above the icons, never as an icon", () => {
    const layout = paneLayout({ width: 250, line: 400, lead: 150 });
    try {
      render(<CalendarLikeRow />);
      const row = screen.getByRole("group", { name: "Actions" });
      expect(row).toHaveAttribute("data-words", "wrap");
      expect(row).toHaveClass("flex-wrap");
      const lead = screen.getByRole("button", { name: "Turn on reminders" });
      expect(lead).toHaveAttribute("data-pane-action", "text");
      expect(lead).toHaveTextContent("Turn on reminders");
      expect(lead).toHaveClass("basis-full", "whitespace-nowrap");
      for (const name of ["Today", "Previous week", "Next week"])
        expect(screen.getByRole("button", { name })).toHaveAttribute("data-pane-action", "icon");
      expect(actionStructureProblems(row.parentElement as HTMLElement)).toEqual([]);
    } finally {
      layout.restore();
    }
  });

  it("re-measures on resize: a row that grows gets its lead back beside the icons", () => {
    const layout = paneLayout({ width: 100, line: 400, lead: 150 });
    try {
      render(<CalendarLikeRow />);
      const row = screen.getByRole("group", { name: "Actions" });
      // Narrower than the lead alone: its icon, the only fit left.
      expect(row).toHaveAttribute("data-words", "none");
      layout.resize(250);
      expect(row).toHaveAttribute("data-words", "wrap");
      layout.resize(500);
      expect(row).toHaveAttribute("data-words", "lead");
      expect(row).toHaveClass("flex-nowrap");
      layout.resize(250);
      expect(row).toHaveAttribute("data-words", "wrap");
    } finally {
      layout.restore();
    }
  });

  it("measures afresh from one line when the lead changes while wrapped", () => {
    const layout = paneLayout({ width: 250, line: 400, lead: 150 });
    try {
      const { rerender } = render(<CalendarLikeRow />);
      const row = screen.getByRole("group", { name: "Actions" });
      expect(row).toHaveAttribute("data-words", "wrap");
      // Reminders on: Today leads, and Today with two icons fits the same row.
      layout.change({ line: 200, lead: 60 });
      rerender(<CalendarLikeRow reminders={false} />);
      expect(row).toHaveAttribute("data-words", "lead");
      layout.resize(600);
      expect(row).toHaveAttribute("data-words", "lead");
    } finally {
      layout.restore();
    }
  });

  it("gives a new lead its words when the lead changes while drawn as an icon", () => {
    const layout = paneLayout({ width: 100, line: 400, lead: 150 });
    try {
      const { rerender } = render(<CalendarLikeRow />);
      const row = screen.getByRole("group", { name: "Actions" });
      expect(row).toHaveAttribute("data-words", "none");
      layout.change({ line: 200, lead: 60 });
      rerender(<CalendarLikeRow reminders={false} />);
      expect(row).toHaveAttribute("data-words", "wrap");
      expect(screen.getByRole("button", { name: "Today" })).toHaveTextContent("Today");
      layout.resize(300);
      expect(row).toHaveAttribute("data-words", "lead");
    } finally {
      layout.restore();
    }
  });

  it("forgets what it learnt when an icon leaves under the same lead", () => {
    const layout = paneLayout({ width: 250, line: 400, lead: 150 });
    try {
      const { rerender } = render(<CalendarLikeRow connect />);
      const row = screen.getByRole("group", { name: "Actions" });
      expect(row).toHaveAttribute("data-words", "wrap");
      // Connect leaves: the lead and three icons now fit the row's 250 px.
      layout.change({ line: 240 });
      rerender(<CalendarLikeRow />);
      expect(row).toHaveAttribute("data-words", "lead");
      layout.resize(250);
      expect(row).toHaveAttribute("data-words", "lead");
    } finally {
      layout.restore();
    }
  });

  it("flags a wrapped row whose lead shares the icons' line", () => {
    const { container } = render(
      <fieldset data-pane-actions="" data-words="wrap">
        <button type="button" data-pane-action="text">
          <svg aria-hidden />
          Turn on reminders
        </button>
        <button type="button" data-pane-action="icon" aria-label="Today" title="Today">
          <svg aria-hidden />
        </button>
      </fieldset>,
    );
    // jsdom draws everything at the top: the lead is not on a line of its own above the icon.
    expect(actionStructureProblems(container)).toContain(
      "the wrapped lead is not on a line of its own above the other actions",
    );
  });

  it("answers like a dialog with words on every action, right-aligned, committing last", () => {
    render(
      <PaneActions align="end" words="all">
        <PaneAction icon={X} onClick={() => {}}>
          Cancel
        </PaneAction>
        <PaneAction icon={Trash2} primary destructive onClick={() => {}}>
          Uninstall
        </PaneAction>
      </PaneActions>,
    );
    const row = screen.getByRole("group", { name: "Actions" });
    expect(row).toHaveClass("justify-end");
    expect(row).toHaveAttribute("data-words", "all");
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveTextContent("Cancel");
    expect(screen.getByRole("button", { name: "Uninstall" })).toHaveClass("bg-bad");
  });

  it("puts chrome as ghost icons at the right of the title row", () => {
    render(
      <PaneTitle
        chrome={
          <PaneAction icon={X} ghost onClick={() => {}}>
            Dismiss
          </PaneAction>
        }
      >
        Today
      </PaneTitle>,
    );
    const dismiss = screen.getByRole("button", { name: "Dismiss" });
    expect(dismiss).toHaveAttribute("title", "Dismiss");
    expect(dismiss.closest("[data-pane-chrome]")).not.toBeNull();
  });

  it("draws nothing, not a stray divider, when every action is hidden", () => {
    // The shape FooterItem's `actions` prop passes: a fragment whose conditionals are all false.
    const hidden = (
      <>
        {false}
        {null}
      </>
    );
    const { container } = render(<PaneActions>{hidden}</PaneActions>);
    expect(container).toBeEmptyDOMElement();
  });

  it("refuses a text link or a hand-made button beside the actions", () => {
    expect(() =>
      render(
        <PaneActions>
          <a href="/doctor" className="underline">
            Open Doctor
          </a>
        </PaneActions>,
      ),
    ).toThrow(/only PaneAction/);
  });

  it("opens outside links in a new tab", () => {
    render(
      <PaneActions>
        <PaneAction icon={ExternalLink} href="https://example.test">
          Open
        </PaneAction>
      </PaneActions>,
    );
    const link = screen.getByRole("link", { name: "Open" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});

/** The calendar pane's row: a primary lead while reminders are off, three navigation icons, and Connect when needed. */
function CalendarLikeRow({ reminders = true, connect = false }: { reminders?: boolean; connect?: boolean }) {
  return (
    <PaneActions>
      {reminders && (
        <PaneAction key="worker" icon={BellRing} primary onClick={() => {}}>
          Turn on reminders
        </PaneAction>
      )}
      <PaneAction key="today" icon={CalendarDays} onClick={() => {}}>
        Today
      </PaneAction>
      <PaneAction key="previous" icon={ChevronLeft} onClick={() => {}}>
        Previous week
      </PaneAction>
      <PaneAction key="next" icon={ChevronRight} onClick={() => {}}>
        Next week
      </PaneAction>
      {connect && (
        <PaneAction key="connect" icon={Plug} onClick={() => {}}>
          Connect
        </PaneAction>
      )}
    </PaneActions>
  );
}

/**
 * A layout jsdom lacks: the row is `width` wide, needs `line` for one line, and the lead's words need `lead`. The row
 * overflows only on one line; a wrapped lead fills the row and overflows only when its words are wider. A wrapped
 * lead sits a line above the icons. `change` sets what the next render's actions need.
 */
function paneLayout(initial: { width: number; line: number; lead: number }) {
  const state = { ...initial };
  // Only connected observers hear a resize, as in a browser: each layout effect replaces the row's observer.
  const observers = new Set<{ callback: () => void }>();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(readonly callback: () => void) {}
      observe() {
        observers.add(this);
      }
      disconnect() {
        observers.delete(this);
      }
      unobserve() {
        observers.delete(this);
      }
    },
  );
  const mode = (element: Element) => element.closest("[data-pane-actions]")?.getAttribute("data-words");
  const isLead = (element: Element) => element.matches("[data-pane-action]:first-child");
  const client = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (this: HTMLElement) {
    if (this.hasAttribute("data-pane-actions")) return state.width;
    if (isLead(this) && mode(this) === "wrap") return state.width - 12;
    return 44;
  });
  const scroll = vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockImplementation(function (this: HTMLElement) {
    if (this.hasAttribute("data-pane-actions"))
      return mode(this) === "lead" ? Math.max(state.width, state.line) : state.width;
    if (isLead(this) && mode(this) === "wrap") return Math.max(state.width - 12, state.lead);
    return 44;
  });
  const box = vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const below = this.matches("[data-pane-action]") && !isLead(this) && mode(this) === "wrap";
    return DOMRect.fromRect({ x: 0, y: below ? 52 : 0, width: 44, height: 44 });
  });
  return {
    change(next: { line?: number; lead?: number }) {
      Object.assign(state, next);
    },
    resize(next: number) {
      state.width = next;
      act(() => {
        for (const observer of [...observers]) observer.callback();
      });
    },
    restore() {
      client.mockRestore();
      scroll.mockRestore();
      box.mockRestore();
    },
  };
}
