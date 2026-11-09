import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { requestMenuAction } from "@/about/menu";
import { answered } from "@/test/chat";
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

  it("clears the search query and puts focus back in the input", async () => {
    mockApi(answers);
    const user = userEvent.setup();
    renderAt("/");
    await screen.findByRole("link", { name: "Weekly digest" });
    await user.keyboard("{Meta>}k{/Meta}");
    const search = await screen.findByRole("combobox", { name: /Search/ });
    const results = within(screen.getByRole("listbox", { name: /Search/ }));
    const firstResult = results.getAllByRole("option")[0]?.textContent ?? "";
    expect(screen.queryByRole("button", { name: "Clear search" })).not.toBeInTheDocument();
    await user.type(search, "log_level");
    expect(screen.getByRole("option", { name: /hz\.log_level/ })).toBeInTheDocument();
    await user.tab();
    const clear = screen.getByRole("button", { name: "Clear search" });
    expect(clear).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(search).toHaveValue("");
    expect(search).toHaveFocus();
    expect(results.getByRole("option", { selected: true })).toHaveTextContent(firstResult);
    expect(screen.queryByRole("button", { name: "Clear search" })).not.toBeInTheDocument();
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
    expect(screen.getByRole("menuitem", { name: "Learn more" })).toHaveAttribute(
      "href",
      "https://heinzel.team/guides/first-steps/",
    );
  });
});

describe("Desktop menu actions", () => {
  it("opens Settings from the app menu", async () => {
    mockApi({ ...answers, "/api/system/config/show": config });
    renderAt("/doctor");
    await waitFor(() => expect(document.querySelector("footer")).not.toBeNull());
    act(() => requestMenuAction("settings"));
    expect(await screen.findByRole("dialog", { name: "Settings" })).toBeInTheDocument();
  });

  it("opens the daemon settings and the Doctor from the app menu", async () => {
    mockApi({ ...answers, "/api/system/config/show": config });
    renderAt("/graph");
    await waitFor(() => expect(document.querySelector("footer")).not.toBeNull());
    act(() => requestMenuAction("daemon"));
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    expect(within(dialog).getByRole("heading", { name: /daemon/i })).toBeInTheDocument();
    await userEvent.setup().keyboard("{Escape}");
    act(() => requestMenuAction("doctor"));
    expect(await screen.findByRole("heading", { name: /doctor/i })).toBeInTheDocument();
  });

  it("opens the changelog panel from the Help menu", async () => {
    mockApi({ ...answers, "/api/system/config/show": config, "/api/system/changelog": "# Changes\n" });
    renderAt("/doctor");
    await waitFor(() => expect(document.querySelector("footer")).not.toBeNull());
    act(() => requestMenuAction("changelog"));
    expect(await screen.findByRole("dialog", { name: "View changelog" })).toBeInTheDocument();
  });

  it("starts a new chat and focuses its composer whether the workspace is open or closed", async () => {
    sessionStorage.setItem("hz.chat", answered.id);
    mockApi({ ...answers, "/api/system/runner/chat/show": answered });
    const user = userEvent.setup();
    renderAt("/doctor");
    const panel = await screen.findByRole("complementary", { name: "Chat" });
    expect(await within(panel).findByText("Make me the lead")).toBeInTheDocument();

    act(() => requestMenuAction("new-chat"));
    expect(within(panel).queryByText("Make me the lead")).not.toBeInTheDocument();
    expect(within(panel).getByLabelText("Your message")).toHaveFocus();
    expect(sessionStorage.getItem("hz.chat")).toBeNull();

    await user.click(within(panel).getByRole("button", { name: "Close the chat" }));
    expect(screen.queryByRole("complementary", { name: "Chat" })).not.toBeInTheDocument();
    act(() => requestMenuAction("new-chat"));
    await waitFor(() => expect(screen.getByLabelText("Your message")).toHaveFocus());
  });

  it("leaves a linked personal chat when starting a new chat from the menu", async () => {
    const id = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
    const { calls } = mockApi({
      ...answers,
      "/api/heinzel/session": { session_id: id, heinzel_id: id, name: "Personal Heinzel", messages: [] },
    });
    renderAt(`/doctor?chat=${id}`);
    expect(await screen.findByRole("heading", { name: "Personal Heinzel" })).toBeInTheDocument();
    act(() => requestMenuAction("new-chat"));
    expect(screen.queryByRole("heading", { name: "Personal Heinzel" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Your message")).toHaveFocus());
    expect(calls.some((request) => request.url.endsWith("/heinzel/new-chat"))).toBe(false);
  });

  it.each([
    { width: 640, placement: "below", chatButton: "Show chat", terminalButton: "Show terminal" },
    { width: 1440, placement: "beside", chatButton: "Close the chat", terminalButton: "Close the terminal" },
  ])(
    "toggles workspace panels from the View menu with the workspace $placement",
    async ({ width, placement, chatButton, terminalButton }) => {
      vi.stubGlobal("innerWidth", width);
      localStorage.setItem("hz.chat.open", "false");
      mockApi({
        ...answers,
        "/api/system/config/show": {
          ...config,
          settings: [
            ...config.settings,
            {
              section: "hz",
              key: "features",
              value: ["sidebar_browser"],
              default: [],
              source: "user",
              env: "HZ_FEATURES",
              description: "Preview features",
            },
          ],
        },
      });
      const user = userEvent.setup();
      renderAt("/doctor");
      await screen.findByRole("button", { name: "Open the browser" });

      for (const [action, name, toolbarName] of [
        ["chat", "Chat", chatButton],
        ["browser", "Browser", "Close the browser"],
        ["terminal", "Terminal", terminalButton],
      ] as const) {
        act(() => requestMenuAction(action));
        const panel = screen.getByRole("tabpanel", { name });
        expect(panel.closest("[data-placement]")).toHaveAttribute("data-placement", placement);
        act(() => requestMenuAction(action));
        expect(panel).not.toBeInTheDocument();

        act(() => requestMenuAction(action));
        const reopened = screen.getByRole("tabpanel", { name });
        await user.click(screen.getByRole("button", { name: toolbarName, pressed: true }));
        if (placement === "below") {
          expect(reopened).toBeVisible();
          act(() => requestMenuAction(action));
        } else {
          expect(reopened).not.toBeInTheDocument();
        }
      }
    },
  );

  it("answers the View, Go and Help menu actions", async () => {
    mockApi({ ...answers, "/api/system/config/show": config });
    renderAt("/doctor");
    await waitFor(() => expect(document.querySelector("footer")).not.toBeNull());
    expect(await screen.findByRole("heading", { name: /doctor/i })).toBeInTheDocument();
    act(() => requestMenuAction("go:plugins"));
    await waitFor(() => expect(screen.queryByRole("heading", { name: /doctor/i })).not.toBeInTheDocument());
    act(() => requestMenuAction("help"));
    expect(await screen.findByRole("dialog", { name: /help/i })).toBeInTheDocument();
    await userEvent.setup().keyboard("{Escape}");
    act(() => requestMenuAction("search"));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});
