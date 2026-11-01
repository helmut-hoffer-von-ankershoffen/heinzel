import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useSearchParams } from "react-router";
import { describe, expect, it } from "vitest";
import { settingsSectionFor } from "@/lib/commandRoute";
import { getRun } from "@/lib/operationRun";
import { runCommand } from "./CommandActions";
import { CommandDialog } from "./CommandDialog";
import { CommandHost } from "./CommandHost";
import { CopyCommand } from "./common";

function Settings() {
  const [params] = useSearchParams();
  return <p data-testid="settings">{params.get("settings") ?? "closed"}</p>;
}

function shell(children: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        {children}
        <CommandHost />
        <Settings />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("a command beside a button", () => {
  it("runs a daemon command in the app, asking first", async () => {
    render(<CopyCommand command="hz system daemon resume" />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Run hz system daemon resume here" }));
    expect(getRun().stage).toBe("confirm");
  });

  it("offers no run button for a command only the CLI has, just copy", () => {
    render(<CopyCommand command="hz team join https://example.test/team.git" />);
    expect(screen.queryByRole("button", { name: /Run|Open the settings/ })).not.toBeInTheDocument();
  });

  it("opens the weather Settings page for a setup command and shows no terminal or dialog", async () => {
    shell(<CopyCommand command="hz weather setup" />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Open the settings for hz weather setup" }));
    expect(screen.getByTestId("settings")).toHaveTextContent("weather");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("shows a command the app cannot do as text to copy, with no way to run it", async () => {
    shell(
      <button type="button" onClick={() => runCommand("hz weather show")}>
        go
      </button>,
    );
    await userEvent.setup().click(screen.getByRole("button", { name: "go" }));
    expect(screen.getByRole("alertdialog", { name: "Run this on your computer" })).toHaveTextContent("hz weather show");
    expect(screen.queryByRole("button", { name: /terminal/i })).not.toBeInTheDocument();
  });

  it("renders the dialog alone with only a Close button", () => {
    render(<CommandDialog command="hz weather show" onClose={() => {}} />);
    expect(screen.getAllByRole("button").map((button) => button.textContent)).not.toContain("Run in terminal");
  });
});

describe("settingsSectionFor", () => {
  it.each([
    ["hz weather setup", "weather"],
    ["hz weather location", "weather"],
    ["hz config set weather.location Berlin", "weather"],
    ["hz config set system.theme.mode dark", "system.theme"],
  ])("maps %s to %s", (command, section) => expect(settingsSectionFor(command)).toBe(section));

  it.each(["hz weather show", "hz clock", "hz config set", "ls weather setup"])("leaves %s alone", (command) =>
    expect(settingsSectionFor(command)).toBeUndefined(),
  );
});
