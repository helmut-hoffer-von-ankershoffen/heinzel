import { render, screen } from "@testing-library/react";
import { Copy, ExternalLink, RefreshCw, Trash2 } from "lucide-react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { PaneAction, PaneActions } from "./PaneActions";

describe("PaneActions", () => {
  it("draws the divider and one unwrapped row of icon-only actions named by their words", () => {
    const { container } = render(
      <MemoryRouter>
        <PaneActions>
          <PaneAction icon={RefreshCw} to="/doctor">
            Open Doctor
          </PaneAction>
          <PaneAction icon={Copy} onClick={() => {}}>
            Copy address
          </PaneAction>
          <PaneAction icon={Trash2} destructive withText="Removing is consequential." onClick={() => {}}>
            Remove
          </PaneAction>
        </PaneActions>
      </MemoryRouter>,
    );
    const row = screen.getByRole("group", { name: "Actions" });
    expect(row).toBe(container.querySelector("[data-pane-actions]"));
    expect(row).toHaveClass("border-t", "flex", "flex-nowrap");
    const doctor = screen.getByRole("link", { name: "Open Doctor" });
    expect(doctor).toHaveAttribute("href", "/doctor");
    expect(doctor).toHaveAttribute("title", "Open Doctor");
    expect(doctor).not.toHaveTextContent("Open Doctor");
    const copy = screen.getByRole("button", { name: "Copy address" });
    expect(copy).toHaveAttribute("title", "Copy address");
    expect(copy).toHaveAttribute("data-pane-action", "icon");
    expect(copy).not.toHaveTextContent("Copy address");
    // Words beside the icon only with a reason: here a consequential action.
    const remove = screen.getByRole("button", { name: "Remove" });
    expect(remove).toHaveClass("text-bad");
    expect(remove).toHaveTextContent("Remove");
    expect(remove).toHaveAttribute("data-pane-action", "text");
    expect(remove.querySelector("svg")).not.toBeNull();
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
