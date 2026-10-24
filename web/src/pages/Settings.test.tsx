import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { expert, shell } from "@/test/fixtures";
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

  it("switches expert mode with the same operations as the CLI, and the navigation follows", async () => {
    const { calls } = mockApi({ ...shell, "/api/system/expert/on": expert });
    const user = userEvent.setup();
    const { container } = renderAt("/settings");
    const toggle = await screen.findByRole("switch", { name: "Expert mode" });
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(toggle).toHaveAttribute("aria-checked", "false");
    const navigation = screen.getByRole("navigation", { name: "Main" });
    expect(within(navigation).queryByRole("link", { name: "Graph" })).not.toBeInTheDocument();
    await user.click(toggle);
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/api/system/expert/on"))).toBe(true);
    expect(within(navigation).getByRole("link", { name: "Graph" })).toBeInTheDocument();
    expect(screen.getByText("hz system expert off")).toBeInTheDocument();
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
