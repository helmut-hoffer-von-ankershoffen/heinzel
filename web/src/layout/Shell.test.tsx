import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { doctor, expert, noTeam, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

const answers = {
  ...shell,
  "/api/system/doctor": doctor,
  "/api/team/show": noTeam,
  "/api/system/daemon/jobs": [],
};

describe("Shell", () => {
  it("has the three landmarks, a skip link and every section in the navigation tree in expert mode", async () => {
    mockApi({ ...answers, "/api/system/expert": expert });
    const { container } = renderAt("/doctor"); // Home is the chat itself, so it has no chat sidebar
    expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("href", "/");
    const navigation = screen.getByRole("navigation", { name: "Main" });
    for (const name of ["Workflow", "Org chart", "Team roster", "Types", "Artifacts"]) {
      expect(await within(navigation).findByRole("link", { name })).toBeInTheDocument();
    }
    expect(within(navigation).getByRole("button", { name: "Artifacts" })).toBeInTheDocument(); // asset types, assets
    expect(await within(navigation).findByRole("link", { name: "Weekly digest" })).toHaveAttribute(
      "href",
      "/contracts/me%3Aweekly-digest",
    );
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Chat" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#content");
    expect(await screen.findByRole("button", { name: /mira/i })).toHaveAttribute("aria-haspopup", "menu");
    await expectAccessible(container);
  });

  it("lists the everyday screens outside expert mode, in the navigation and in search", async () => {
    mockApi(answers);
    const user = userEvent.setup();
    renderAt("/");
    const navigation = screen.getByRole("navigation", { name: "Main" });
    expect(await within(navigation).findByRole("link", { name: "Team roster" })).toBeInTheDocument();
    expect(within(navigation).queryByRole("link", { name: "Doctor" })).not.toBeInTheDocument(); // hz itself: account menu
    expect(within(navigation).getByRole("link", { name: "Team roster" })).toBeInTheDocument();
    expect(await within(navigation).findByRole("link", { name: "Weekly digest" })).toBeInTheDocument();
    for (const name of ["Workflow", "Org chart", "Types", "Artifacts"]) {
      expect(within(navigation).queryByRole("link", { name })).not.toBeInTheDocument();
    }
    expect(within(navigation).queryByText("Artifacts")).not.toBeInTheDocument();
    await user.keyboard("{Meta>}k{/Meta}");
    await user.type(await screen.findByRole("combobox", { name: /Search/ }), "Workflow");
    expect(screen.getByText("Nothing matches.")).toBeInTheDocument();
  });

  it("searches settings, contracts and screens with ⌘K and goes to the one chosen", async () => {
    mockApi(answers);
    const user = userEvent.setup();
    renderAt("/");
    await screen.findByRole("link", { name: "Weekly digest" });
    await user.keyboard("{Meta>}k{/Meta}");
    const search = await screen.findByRole("combobox", { name: /Search/ });
    await user.type(search, "log_level");
    expect(screen.getByRole("option", { name: /hz\.log_level/ })).toBeInTheDocument();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("dialog", { name: "Settings" })).toBeInTheDocument();
  });

  it("switches between light and dark and remembers it", async () => {
    mockApi(answers);
    const user = userEvent.setup();
    renderAt("/");
    const before = document.documentElement.classList.contains("dark");
    await user.click(screen.getByRole("button", { name: "Switch between light and dark" }));
    expect(document.documentElement.classList.contains("dark")).toBe(!before);
    expect(localStorage.getItem("hz.theme")).toBe(before ? "light" : "dark");
  });

  it("collapses the navigation and the chat", async () => {
    mockApi(answers);
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.click(screen.getAllByRole("button", { name: "Close the chat" })[0] as HTMLElement);
    expect(screen.queryByRole("complementary", { name: "Chat" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Chat with your Heinzelmännchen" }));
    expect(screen.getByRole("complementary", { name: "Chat" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Collapse navigation" }));
    expect(screen.getByRole("button", { name: "Expand navigation" })).toHaveAttribute("aria-expanded", "false");
  });
});
