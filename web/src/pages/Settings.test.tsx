import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { expert, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

describe("Settings dialog", () => {
  it("opens over the screen with a module's settings, each with its control", async () => {
    mockApi(shell);
    const { container } = renderAt("/?settings=hz");
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    expect(await within(dialog).findByLabelText("Log level")).toHaveValue("INFO");
    await expectAccessible(container);
  });

  it("switches expert mode with the same operations as the CLI, and the navigation follows", async () => {
    const { calls } = mockApi({ ...shell, "/api/system/expert/on": expert });
    const user = userEvent.setup();
    const { container } = renderAt("/?settings=hz");
    const toggle = await screen.findByRole("switch", { name: "Expert mode" });
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(toggle).toHaveAttribute("aria-checked", "false");
    const navigation = screen.getByRole("navigation", { name: "Main" });
    expect(within(navigation).queryByRole("link", { name: "Workflow" })).not.toBeInTheDocument();
    await user.click(toggle);
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/api/system/expert/on"))).toBe(true);
    expect(within(navigation).getByRole("link", { name: "Workflow" })).toBeInTheDocument();
    expect(screen.getByText(/^On: the navigation/)).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("searches across every module", async () => {
    mockApi(shell);
    const user = userEvent.setup();
    renderAt("/?settings=hz");
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    await user.type(await within(dialog).findByRole("searchbox"), "ask_max");
    expect(await within(dialog).findByRole("heading", { name: "[system.runner]" })).toBeInTheDocument();
    expect(within(dialog).queryByLabelText("Log level")).not.toBeInTheDocument();
  });

  it("opens searching from an older /settings?q= link", async () => {
    mockApi(shell);
    renderAt("/settings?q=ask_max_usd");
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    expect(await within(dialog).findByRole("heading", { name: "[system.runner]" })).toBeInTheDocument();
  });
});
