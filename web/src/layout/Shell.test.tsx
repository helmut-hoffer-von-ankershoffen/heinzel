import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { doctor, noTeam, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

const answers = {
  ...shell,
  "/api/system/doctor": doctor,
  "/api/team/show": noTeam,
  "/api/system/daemon/jobs": [],
};

describe("Shell", () => {
  it("has the three landmarks, a skip link and every section in the navigation tree", async () => {
    mockApi(answers);
    const { container } = renderAt("/");
    const navigation = screen.getByRole("navigation", { name: "Main" });
    for (const name of ["Home", "Settings", "Graph", "Org chart", "Team roster", "Asset types", "Assets", "Doctor"]) {
      expect(within(navigation).getByRole("link", { name })).toBeInTheDocument();
    }
    expect(await within(navigation).findByRole("link", { name: "Weekly digest" })).toHaveAttribute(
      "href",
      "/contracts/me%3Aweekly-digest",
    );
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Ask" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#content");
    expect(await screen.findByRole("link", { name: /mira/ })).toBeInTheDocument();
    await expectAccessible(container);
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
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
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
    renderAt("/");
    await user.click(screen.getByRole("button", { name: "Collapse the chat" }));
    expect(screen.queryByRole("complementary", { name: "Ask" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open the chat" }));
    expect(screen.getByRole("complementary", { name: "Ask" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Collapse navigation" }));
    expect(screen.getByRole("button", { name: "Expand navigation" })).toHaveAttribute("aria-expanded", "false");
  });
});
