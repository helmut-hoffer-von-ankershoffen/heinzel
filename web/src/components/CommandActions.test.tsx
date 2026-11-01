import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getRun } from "@/lib/operationRun";
import { connectTerminal, OPEN_TERMINAL } from "@/lib/terminal";
import { CommandDialog } from "./CommandDialog";
import { CopyCommand } from "./common";

afterEach(() => connectTerminal(null));

describe("a command beside a button", () => {
  it("runs a daemon command in the app, asking first, and types nothing", async () => {
    const sent: string[] = [];
    connectTerminal((text) => sent.push(text));
    render(<CopyCommand command="hz system daemon resume" />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Run hz system daemon resume here" }));
    expect(getRun().stage).toBe("confirm");
    expect(sent).toEqual([]);
  });

  it("labels the terminal button for a command only the CLI has, and types only when it is pressed", async () => {
    const sent: string[] = [];
    connectTerminal((text) => sent.push(text));
    const opened = vi.fn();
    window.addEventListener(OPEN_TERMINAL, opened);
    render(<CopyCommand command="hz team join https://example.test/team.git" />);
    expect(sent).toEqual([]);
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Run in terminal: hz team join https://example.test/team.git" }));
    expect(screen.getByText("Run in terminal")).toBeInTheDocument();
    expect(sent).toHaveLength(1);
    window.removeEventListener(OPEN_TERMINAL, opened);
  });

  it("shows a contributed command as text to copy; the terminal only gets it when the button is pressed", async () => {
    const sent: string[] = [];
    connectTerminal((text) => sent.push(text));
    const close = vi.fn();
    render(<CommandDialog command="hz weather show" onClose={close} />);
    expect(screen.getByRole("alertdialog", { name: "This is run in a terminal" })).toHaveTextContent("hz weather show");
    expect(sent).toEqual([]);
    await userEvent.setup().click(screen.getByRole("button", { name: "Run in terminal" }));
    expect(sent).toEqual(["hz weather show\r"]);
    expect(close).toHaveBeenCalled();
  });
});
