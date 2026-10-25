import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { expectAccessible } from "@/test/render";
import { SearchField } from "./search-field";

const box = () => screen.getByRole("searchbox").closest("label") as HTMLElement;

describe("SearchField", () => {
  it("is one box: the input has no outline of its own, and the box rings only for keyboard focus", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <>
        <button type="button">before</button>
        <SearchField label="Filter" />
      </>,
    );
    expect(screen.getByRole("searchbox")).toHaveClass("outline-none");
    expect(box()).not.toHaveClass("ring-1");
    await user.tab();
    await user.tab();
    expect(screen.getByRole("searchbox")).toHaveFocus();
    expect(box()).toHaveClass("ring-1");
    await expectAccessible(container);
    await user.tab();
    expect(box()).not.toHaveClass("ring-1");
  });

  it("shows no ring when it is focused for you on opening, and again once you leave and return", async () => {
    const user = userEvent.setup();
    render(
      <>
        <SearchField label="Filter" quietFocus />
        <button type="button">after</button>
      </>,
    );
    expect(screen.getByRole("searchbox")).toHaveFocus();
    expect(box()).not.toHaveClass("ring-1");
    await user.type(screen.getByRole("searchbox"), "clock");
    expect(box()).toHaveClass("ring-1");
    expect(screen.getByRole("searchbox")).toHaveAttribute("data-focus-ring", "parent");
    await user.tab();
    await user.tab({ shift: true });
    expect(box()).toHaveClass("ring-1");
  });

  it("defers initial focus while startup is inert, then keeps a single quiet/typing indicator", async () => {
    const { container } = render(
      <div inert>
        <SearchField label="Filter" quietFocus />
      </div>,
    );
    const input = screen.getByRole("searchbox", { hidden: true });
    expect(input).not.toHaveFocus();
    await act(() => {
      container.firstElementChild?.removeAttribute("inert");
    });
    await waitFor(() => expect(input).toHaveFocus());
    expect(box()).not.toHaveClass("ring-1");
    await userEvent.setup().type(input, "volume");
    expect(box()).toHaveClass("ring-1");
    expect(input).toHaveAttribute("data-focus-ring", "parent");
  });

  it("does not steal a newer editable field's focus when an inert ancestor clears", async () => {
    const { container } = render(
      <>
        <input aria-label="Other edit" />
        <div inert>
          <SearchField label="Filter" quietFocus />
        </div>
      </>,
    );
    const other = screen.getByLabelText("Other edit");
    other.focus();
    await act(() => {
      container.querySelector("[inert]")?.removeAttribute("inert");
    });
    expect(other).toHaveFocus();
  });
});
