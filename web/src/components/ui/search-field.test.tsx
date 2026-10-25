import { render, screen } from "@testing-library/react";
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
    expect(box()).not.toHaveClass("ring-2");
    await user.tab();
    await user.tab();
    expect(screen.getByRole("searchbox")).toHaveFocus();
    expect(box()).toHaveClass("ring-2");
    await expectAccessible(container);
    await user.tab();
    expect(box()).not.toHaveClass("ring-2");
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
    expect(box()).not.toHaveClass("ring-2");
    await user.tab();
    await user.tab({ shift: true });
    expect(box()).toHaveClass("ring-2");
  });
});
