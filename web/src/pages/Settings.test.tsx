import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

describe("Settings", () => {
  it("groups every setting by section with its value, default, source and the command that sets it", async () => {
    mockApi(shell);
    const { container } = renderAt("/settings");
    const section = (await screen.findByRole("heading", { name: "[hz]" })).closest("section") as HTMLElement;
    const row = within(section).getByRole("row", { name: /log_level/ });
    expect(within(row).getByText("INFO")).toBeInTheDocument();
    expect(within(row).getByText("WARNING")).toBeInTheDocument();
    expect(within(row).getByText("user")).toBeInTheDocument();
    expect(within(row).getByText("hz system config set hz.log_level INFO")).toBeInTheDocument();
    expect(within(row).getByText("hz system config unset hz.log_level")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "[system.runner]" })).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("filters settings by key or description", async () => {
    mockApi(shell);
    const user = userEvent.setup();
    renderAt("/settings");
    await user.type(await screen.findByLabelText("Filter settings"), "role");
    expect(screen.getByRole("heading", { name: "[org]" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "[hz]" })).not.toBeInTheDocument();
  });

  it("opens filtered from a search result's link", async () => {
    mockApi(shell);
    renderAt("/settings?q=ask_max_usd");
    expect(await screen.findByRole("heading", { name: "[system.runner]" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "[org]" })).not.toBeInTheDocument();
  });
});
