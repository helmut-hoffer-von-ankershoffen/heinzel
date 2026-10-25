import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OPEN_TERMINAL } from "@/lib/terminal";
import { expectAccessible } from "@/test/render";
import { Prose } from "./Prose";

const show = (markdown: string) =>
  render(
    <MemoryRouter>
      <Prose>{markdown}</Prose>
    </MemoryRouter>,
  );

afterEach(() => vi.restoreAllMocks());

describe("Markdown in the app (GitHub-flavoured)", () => {
  it("renders headings, emphasis, strikethrough, inline code, quotes, rules and line breaks", async () => {
    const { container } = show(
      "# Title\n\n## Part\n\n**bold**, *em*, ~~gone~~ and `hz ui`.\n\n> quoted\n\n---\n\nline one  \nline two",
    );
    expect(screen.getByRole("heading", { level: 1, name: "Title" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Part" })).toBeInTheDocument();
    expect(screen.getByText("bold").tagName).toBe("STRONG");
    expect(screen.getByText("em").tagName).toBe("EM");
    expect(screen.getByText("gone").tagName).toBe("DEL");
    expect(screen.getByText("hz ui").tagName).toBe("CODE");
    expect(container.querySelector("blockquote")).toHaveTextContent("quoted");
    expect(container.querySelector("hr")).not.toBeNull();
    expect(container.querySelector("br")).not.toBeNull();
    await expectAccessible(container);
  });

  it("renders nested, ordered and task lists, and tables that scroll sideways", () => {
    const { container } = show(
      "1. first\n2. second\n   - inner\n\n- [x] done\n- [ ] open\n\n| a | b |\n| - | - |\n| 1 | 2 |",
    );
    expect(container.querySelector("ol li ul li")).toHaveTextContent("inner");
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes.map((box) => (box as HTMLInputElement).checked)).toEqual([true, false]);
    expect(boxes.every((box) => (box as HTMLInputElement).disabled)).toBe(true);
    const table = screen.getByRole("table");
    expect(within(table).getByRole("columnheader", { name: "a" })).toBeInTheDocument();
    expect(table.parentElement).toHaveClass("overflow-x-auto");
  });

  it("opens outside links in a new tab, keeps app paths in the app and drops other schemes", () => {
    show("[site](https://example.com) [mail](mailto:a@example.com) [doctor](/doctor) [bad](javascript:alert(1))");
    const site = screen.getByRole("link", { name: "site" });
    expect(site).toHaveAttribute("target", "_blank");
    expect(site).toHaveAttribute("rel", "noreferrer noopener");
    expect(screen.getByRole("link", { name: "mail" })).toHaveAttribute("href", "mailto:a@example.com");
    expect(screen.getByRole("link", { name: "doctor" })).toHaveAttribute("href", "/doctor");
    expect(screen.getByRole("link", { name: "doctor" })).not.toHaveAttribute("target");
    expect(screen.queryByRole("link", { name: "bad" })).not.toBeInTheDocument();
    expect(screen.getByText("bad")).toBeInTheDocument();
  });

  it("never loads an image or renders raw HTML", () => {
    const { container } = show('![a chart](https://example.com/c.png)\n\n<img src="x.png"><b>raw</b> text');
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByRole("link", { name: "Image: a chart" })).toHaveAttribute("href", "https://example.com/c.png");
    expect(container.querySelector("b")).toBeNull();
  });

  it("shows fenced code with Copy, and Run in the terminal for a one-line shell command", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText");
    const opened = vi.fn();
    window.addEventListener(OPEN_TERMINAL, opened);
    show("```sh\n$ hz system doctor\n```\n\n```python\nprint(1)\n```");
    const [shell, python] = screen.getAllByRole("button", { name: "Copy the code" });
    await user.click(python as HTMLElement);
    expect(writeText).toHaveBeenCalledWith("print(1)\n");
    expect(shell).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /in the terminal/ })).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Run hz system doctor in the terminal" }));
    expect(opened).toHaveBeenCalled();
    window.removeEventListener(OPEN_TERMINAL, opened);
  });

  it("renders a streaming answer cut mid-way without losing what came", () => {
    const { container } = show("Here is **half of a bold and\n\n```sh\nhz sy");
    expect(container).toHaveTextContent("Here is **half of a bold and");
    expect(container.querySelector("pre")).toHaveTextContent("hz sy");
  });
});
