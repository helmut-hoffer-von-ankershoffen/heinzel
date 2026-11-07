// Dialogs answer a question: one right-aligned row, every action with words, Cancel first and the committing action
// last, red when it destroys something (design/README.md, "Actions").
import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { CommandDialog } from "@/components/CommandDialog";
import { assertActionStructure } from "@/components/PaneActions";
import { RestartAndApply } from "@/lifecycle/RestartAndApply";

const names = (container: HTMLElement) =>
  within(container)
    .getAllByRole("button")
    .map((button) => button.textContent?.trim());

it("restart confirmation: Cancel, then the committing action, both with words, in one row", () => {
  render(<RestartAndApply target="Clock" changes={["Primary time zone"]} />);
  fireEvent.click(screen.getByRole("button", { name: "Restart and apply" }));
  const dialog = screen.getByRole("alertdialog", { name: "Restart and apply Clock" });
  expect(names(dialog)).toEqual(["Cancel", "Restart and apply Clock"]);
  const row = dialog.querySelector("[data-pane-actions]");
  expect(row).toHaveAttribute("data-words", "all");
  expect(row).toHaveClass("justify-end");
  for (const button of within(dialog).getAllByRole("button"))
    expect(button).toHaveAttribute("data-pane-action", "text");
  expect(within(dialog).getByRole("button", { name: "Restart and apply Clock" }).className).toMatch(
    /bg-primary|bg-bad/,
  );
  expect(() => assertActionStructure(dialog)).not.toThrow();
});

it("command dialog: Close first, the committing action last", () => {
  const onClose = vi.fn();
  render(<CommandDialog command="hz system doctor" onClose={onClose} />);
  const dialog = screen.getByRole("alertdialog");
  const row = dialog.querySelector("[data-pane-actions]") as HTMLElement;
  expect(names(row)).toHaveLength(2);
  expect(names(row)[0]).toBe("Close");
  fireEvent.click(within(row).getByRole("button", { name: "Close" }));
  expect(onClose).toHaveBeenCalled();
});
