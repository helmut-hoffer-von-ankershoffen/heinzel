import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ToolEvents } from "./ToolEvents";

const item = (seq: number, tool: string, status: string) =>
  ({ seq, tool, status, at: "2026-11-11T10:00:00Z" }) as Parameters<typeof ToolEvents>[0]["items"][number];

describe("The tool events of an answer", () => {
  it("counts the events under the label", () => {
    render(<ToolEvents label="Tools" items={[item(1, "read", "ok"), item(2, "write", "ok")]} />);
    expect(screen.getByText(/Tools · 2 events/)).toBeInTheDocument();
  });

  it("lists them in journal order whatever order they arrived in", () => {
    const items = [item(3, "third", "ok"), item(1, "first", "ok"), item(2, "second", "failed")];
    render(<ToolEvents label="Tools" items={items} />);
    const rows = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(rows.map((row) => row.querySelector("span")?.textContent)).toEqual([
      "first · ok",
      "second · failed",
      "third · ok",
    ]);
  });

  it("does not reorder the list it was given", () => {
    const items = [item(2, "b", "ok"), item(1, "a", "ok")];
    render(<ToolEvents label="Tools" items={items} />);
    expect(items.map((i) => i.seq)).toEqual([2, 1]);
  });

  it("marks each time with its machine-readable instant", () => {
    const { container } = render(<ToolEvents label="Tools" items={[item(1, "read", "ok")]} />);
    expect(container.querySelector("time")).toHaveAttribute("datetime", "2026-11-11T10:00:00Z");
  });
});
