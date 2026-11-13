import { act, render, screen } from "@testing-library/react";
import {
  BellRing,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Copy,
  ExternalLink,
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

/** The calendar pane's row on a phone: a primary lead and three navigation icons. */
function CalendarLikeRow() {
  return (
    <PaneActions>
      <PaneAction icon={BellRing} primary onClick={() => {}}>
        Turn on reminders
      </PaneAction>
      <PaneAction icon={CalendarDays} onClick={() => {}}>
        Today
      </PaneAction>
      <PaneAction icon={ChevronLeft} onClick={() => {}}>
        Previous week
      </PaneAction>
      <PaneAction icon={ChevronRight} onClick={() => {}}>
        Next week
      </PaneAction>
    </PaneActions>
  );
}

/**
 * A layout jsdom lacks: the row is `width` wide, needs `line` for one line, and the lead's words need `lead`. The row
 * overflows only on one line; a wrapped lead fills the row and overflows only when its words are wider.
 */
function paneLayout(initial: { width: number; line: number; lead: number }) {
  let width = initial.width;
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
    if (this.hasAttribute("data-pane-actions")) return width;
    if (isLead(this) && mode(this) === "wrap") return width - 12;
    return 44;
  });
  const scroll = vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockImplementation(function (this: HTMLElement) {
    if (this.hasAttribute("data-pane-actions")) return mode(this) === "lead" ? Math.max(width, initial.line) : width;
    if (isLead(this) && mode(this) === "wrap") return Math.max(width - 12, initial.lead);
    return 44;
  });
  return {
    resize(next: number) {
      width = next;
      act(() => {
        for (const observer of [...observers]) observer.callback();
      });
    },
    restore() {
      client.mockRestore();
      scroll.mockRestore();
    },
  };
}
