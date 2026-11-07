import { render, screen } from "@testing-library/react";
import { Copy, RefreshCw, Trash2 } from "lucide-react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { PaneAction, PaneActions, PaneIconAction, PaneTitle } from "./PaneActions";

describe("PaneActions", () => {
  it("draws the divider and one row of actions", () => {
    const { container } = render(
      <MemoryRouter>
        <PaneActions>
          <PaneAction icon={RefreshCw} to="/doctor">
            Open Doctor
          </PaneAction>
          <PaneAction icon={Copy} iconOnly onClick={() => {}}>
            Copy address
          </PaneAction>
          <PaneAction icon={Trash2} destructive onClick={() => {}}>
            Remove
          </PaneAction>
        </PaneActions>
      </MemoryRouter>,
    );
    const row = container.querySelector("[data-pane-actions]");
    expect(row).toHaveClass("border-t", "flex", "flex-wrap");
    expect(screen.getByRole("link", { name: "Open Doctor" })).toHaveAttribute("href", "/doctor");
    const copy = screen.getByRole("button", { name: "Copy address" });
    expect(copy).toHaveAttribute("title", "Copy address");
    expect(copy).not.toHaveTextContent("Copy address");
    expect(screen.getByRole("button", { name: "Remove" })).toHaveClass("text-bad");
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
        <PaneAction href="https://example.test">Open</PaneAction>
      </PaneActions>,
    );
    const link = screen.getByRole("link", { name: "Open" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});

describe("PaneIconAction", () => {
  it("names itself and shows the name as its tooltip, in the title row", () => {
    render(
      <MemoryRouter>
        <PaneTitle action={<PaneIconAction icon={RefreshCw} label="Open chat" to="/" />}>Heinzels</PaneTitle>
      </MemoryRouter>,
    );
    const link = screen.getByRole("link", { name: "Open chat" });
    expect(link).toHaveAttribute("title", "Open chat");
    expect(link).toHaveClass("size-8");
  });
});
