import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { config, doctor, expert, noTeam, profile, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

const answers = {
  ...shell,
  "/api/system/doctor": doctor,
  "/api/team/show": noTeam,
  "/api/system/daemon/jobs": [],
};

describe("Shell", () => {
  it("keeps the audio visualizer off unless the System setting is enabled", async () => {
    const first = mockApi({ ...answers, "/api/system/config/show": config });
    const { unmount } = renderAt("/doctor");
    await waitFor(() => expect(first.calls.some((request) => request.url.includes("/system/config/show"))).toBe(true));
    expect(screen.queryByRole("region", { name: "Audio visualizer" })).not.toBeInTheDocument();
    unmount();

    const enabled = {
      ...config,
      settings: [
        ...config.settings,
        {
          section: "system.ui",
          key: "audio_visualizer",
          value: true,
          default: false,
          source: "user" as const,
          env: "HZ_SYSTEM_UI__AUDIO_VISUALIZER",
          description: "Show the optional audio sharing visualizer.",
        },
      ],
    };
    mockApi({ ...answers, "/api/system/config/show": enabled });
    renderAt("/doctor");
    const visualizer = await screen.findByRole("region", { name: "Audio visualizer" });
    expect(visualizer).toHaveClass("self-stretch", "min-h-0");
  });

  it("keeps one mounted footer and its focus when the side panel moves below and back", async () => {
    mockApi({ ...answers, "/api/system/info": { version: "0.7.12", modules: [] } });
    const initialWidth = window.innerWidth;
    try {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 1440 });
      renderAt("/doctor");
      const version = await screen.findByRole("button", { name: /^v\d/ });
      const footer = screen.getByRole("contentinfo");
      version.focus();
      for (const width of [640, 1440]) {
        await act(() => {
          Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
          window.dispatchEvent(new Event("resize"));
        });
        expect(screen.getByRole("contentinfo")).toBe(footer);
        expect(screen.getByRole("button", { name: /^v\d/ })).toBe(version);
        expect(version).toHaveFocus();
      }
    } finally {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: initialWidth });
    }
  });

  it("has the three landmarks, a skip link and every section in the navigation tree in expert mode", async () => {
    mockApi({ ...answers, "/api/system/expert": expert });
    const { container } = renderAt("/doctor"); // Home is the chat itself, so it has no chat sidebar
    expect(screen.getByRole("link", { name: "Today" })).toHaveAttribute("href", "/");
    const navigation = screen.getByRole("navigation", { name: "Main" });
    for (const name of ["Workflow", "Org chart", "Team roster", "Types", "Inventory"]) {
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
    for (const name of ["Workflow", "Org chart", "Types", "Inventory"]) {
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

  it("keeps the real user's initial as the last compact account control", async () => {
    mockApi({ ...answers, "/api/org/show": { ...profile, name: "Ada Example", handle: "ada" } });
    const user = userEvent.setup();
    renderAt("/");
    await user.click(screen.getByRole("button", { name: "Collapse navigation" }));
    const account = await screen.findByRole("button", { name: "Ada Example" });
    expect(account).toHaveTextContent("A");
    expect(account).toHaveAttribute("title", "Ada Example · engineering manager");
    expect(account.nextElementSibling).toBeNull();
    expect(
      within(account.parentElement as HTMLElement).getByRole("button", { name: "Choose a soundscape" }),
    ).toBeVisible();
    account.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("menuitem", { name: "Profile" })).toBeVisible();
  });
});
