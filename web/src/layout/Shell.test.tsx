import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { requestMenuAction } from "@/about/menu";
import { createDraftPath } from "@/lib/chatIntent";
import { config, doctor, expert, noTeam, profile, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

/** One Heinzel, Helmi, so the chat opens on it (ADR 0157). */
const helmi = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const withHelmi = {
  "/api/heinzel/sessions": {
    sessions: [{ session_id: helmi, name: "Helmi", state: "awake", last_seq: 1, updated_at: "2026-11-14T09:00:00Z" }],
  },
  "/api/heinzel/session": { session_id: helmi, heinzel_id: helmi, name: "Helmi", messages: [] },
};

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

  it("opens the Chat tab on a phone when the link names a conversation", async () => {
    mockApi({ ...answers });
    const initialWidth = window.innerWidth;
    try {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
      renderAt("/doctor?chat=hz-01ARZ3NDEKTSV4RRFFQ69G5FAV");
      await waitFor(() => expect(screen.getByRole("tab", { name: "Chat" })).toHaveAttribute("aria-selected", "true"));
    } finally {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: initialWidth });
    }
  });

  it("opens the Page tab on a phone when the chat switcher asks for a new Heinzel on the same page", async () => {
    mockApi({ ...answers, ...withHelmi });
    const initialWidth = window.innerWidth;
    try {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
      renderAt("/heinzel");
      const user = userEvent.setup();
      await user.click(await screen.findByRole("tab", { name: "Chat" }));
      await waitFor(() => expect(screen.getByRole("tab", { name: "Chat" })).toHaveAttribute("aria-selected", "true"));
      // Only the query changes (`?create=1` on /heinzel): the pathname alone used to decide the tab.
      await user.click(await screen.findByRole("button", { name: /^Helmi, .+\. Switch Heinzel$/ }));
      const switcher = await screen.findByRole("dialog", { name: "Your Heinzels" });
      await user.click(within(switcher).getByRole("button", { name: "New Heinzel" }));
      await waitFor(() => expect(screen.getByRole("tab", { name: "Page" })).toHaveAttribute("aria-selected", "true"));
    } finally {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: initialWidth });
    }
  });

  it("keeps Chat chosen on a phone when it is pressed right after About opens the Heinzel's page", async () => {
    mockApi({ ...answers, ...withHelmi });
    const initialWidth = window.innerWidth;
    try {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
      renderAt("/doctor");
      const user = userEvent.setup();
      await user.click(await screen.findByRole("tab", { name: "Chat" }));
      await user.click(await screen.findByRole("button", { name: "More: search, about" }));
      await user.click(
        within(await screen.findByRole("dialog", { name: "More" })).getByRole("button", { name: /^About / }),
      );
      // The very next press, before anything else settles: the person's choice of Chat wins over the new address.
      await user.click(screen.getByRole("tab", { name: "Chat" }));
      await screen.findByRole("heading", { level: 1, name: /, your Heinzel$/ });
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 50));
      });
      expect(screen.getByRole("tab", { name: "Chat" })).toHaveAttribute("aria-selected", "true");
    } finally {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: initialWidth });
    }
  });

  it("keeps a linked conversation in the link to a new Heinzel's draft", () => {
    expect(createDraftPath(new URLSearchParams({ chat: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV", panel: "changelog" }))).toBe(
      "/heinzel?chat=hz-01ARZ3NDEKTSV4RRFFQ69G5FAV&create=1",
    );
    expect(createDraftPath(new URLSearchParams())).toBe("/heinzel?create=1");
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
  afterEach(() => Reflect.deleteProperty(window, "heinzelDesktop"));
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

  it("opens the chat on the default Heinzel from the menu's New Chat, whether the chat is open or closed", async () => {
    mockApi({ ...answers, ...withHelmi });
    const user = userEvent.setup();
    renderAt("/doctor");
    const panel = await screen.findByRole("complementary", { name: "Chat" });
    const composer = await within(panel).findByLabelText("Message Helmi");
    await waitFor(() => expect(composer).toBeEnabled());
    expect(composer).not.toHaveFocus();
    // With the chat open, New Chat puts the cursor in the default Heinzel's composer.
    act(() => requestMenuAction("new-chat"));
    await waitFor(() => expect(within(panel).getByLabelText("Message Helmi")).toHaveFocus());
    await user.click(await within(panel).findByRole("button", { name: "Close the chat" }));
    expect(screen.queryByRole("complementary", { name: "Chat" })).not.toBeInTheDocument();
    act(() => requestMenuAction("new-chat"));
    const reopened = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await reopened.findByRole("button", { name: /^Helmi, .+\. Switch Heinzel$/ })).toBeInTheDocument();
    // Closed, it opens the chat and focuses the composer once it is ready.
    await waitFor(() => expect(reopened.getByLabelText("Message Helmi")).toHaveFocus());
    // There is no general chat to start: New Chat never opens one.
    expect(screen.queryByLabelText("Your message")).not.toBeInTheDocument();
  });

  it("leaves a linked Heinzel's chat for the default one when New Chat is chosen from the menu", async () => {
    const id = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
    const { calls } = mockApi({
      ...answers,
      "/api/heinzel/session": { session_id: id, heinzel_id: id, name: "Personal Heinzel", messages: [] },
    });
    renderAt(`/doctor?chat=${id}`);
    expect(await screen.findByRole("button", { name: /^Personal Heinzel, .+\. Switch Heinzel$/ })).toBeInTheDocument();
    act(() => requestMenuAction("new-chat"));
    // No Heinzel is listed, so the default is the first start.
    expect(await screen.findByRole("heading", { name: "Meet your first Heinzelmännchen" })).toBeInTheDocument();
    // The first start takes the cursor in its name field.
    await waitFor(() => expect(screen.getByLabelText("What do you call it?")).toHaveFocus());
    expect(screen.queryByRole("button", { name: /^Personal Heinzel, .+\. Switch Heinzel$/ })).not.toBeInTheDocument();
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
      Object.defineProperty(window, "heinzelDesktop", {
        value: { browser: vi.fn().mockResolvedValue({ status: "empty" }) },
        configurable: true,
      });
      mockApi(answers);
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

  it("opens regular Browser settings from the browser toolbar", async () => {
    Object.defineProperty(window, "heinzelDesktop", {
      value: { browser: vi.fn().mockResolvedValue({ status: "empty" }) },
      configurable: true,
    });
    mockApi({ ...answers, "/api/system/config/show": config });
    const user = userEvent.setup();
    renderAt("/doctor");
    await screen.findByRole("button", { name: "Open the browser" });
    act(() => requestMenuAction("browser"));
    await user.click(screen.getByRole("button", { name: "Browser settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    expect(await within(dialog).findByRole("heading", { name: "Browser" })).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "Clear browsing data" })).toBeVisible();
  });

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

  it("reveals Updates from the desktop menu when a full-screen terminal hides the narrow footer", async () => {
    vi.stubGlobal("innerWidth", 640);
    const { calls } = mockApi({
      ...answers,
      "/api/auto-update/check": {
        enabled: true,
        reason: null,
        phase: "idle",
        version: "0.1.0",
        core: null,
        plugins: {},
        seconds_left: null,
        apply_at: null,
        next_window_at: null,
        postponements: 0,
        message: null,
      },
    });
    renderAt("/doctor");
    act(() => requestMenuAction("terminal"));
    const terminal = await screen.findByRole("tabpanel", { name: "Terminal" });
    await userEvent.setup().click(within(terminal).getByRole("button", { name: "Full screen" }));
    expect(screen.queryByRole("contentinfo")).not.toBeInTheDocument();

    act(() => requestMenuAction("updates"));
    expect(await screen.findByRole("contentinfo")).toBeVisible();
    expect(await screen.findByRole("button", { name: "Check for updates" })).toBeVisible();
    expect(within(terminal).getByRole("button", { name: "Full screen" })).toBeVisible();
    const checks = calls.filter((request) => request.url.endsWith("/api/auto-update/check"));
    expect(checks).toHaveLength(1);
    expect(checks[0]?.method).toBe("POST");
  });
});
