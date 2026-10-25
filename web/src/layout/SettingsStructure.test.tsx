import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { SchemaConfigReport, SchemaPluginInfo } from "@/api/client";
import { config, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { toToml } from "./settingSchema";
import { displayName, inWords, PAGES, pageOf, settingsNav } from "./settingsSections";

const plugin = (overrides: Partial<SchemaPluginInfo>): SchemaPluginInfo =>
  ({
    distribution: `heinzel-${overrides.name}`,
    version: "0.3.0",
    modules: [overrides.name ?? ""],
    summary: "",
    source_kind: "git",
    source_url: null,
    revision: null,
    homepage: null,
    first_party: true,
    enabled: true,
    error: null,
    layers: [],
    capabilities: [],
    playbooks: [],
    themes: [],
    kinds: [],
    ...overrides,
  }) as SchemaPluginInfo;

const github = plugin({
  name: "github",
  summary: "Read pull requests and issues from GitHub: the code-host and tracker capabilities.",
  capabilities: ["github:code-host", "github:tracker"],
  kinds: ["connector"],
});
const clock = plugin({
  name: "clock",
  summary: "World Clock: what time it is for everyone on the team.",
  kinds: ["capability"],
});

const zonesSchema = {
  type: "array",
  title: "Zones",
  items: {
    type: "object",
    title: "Zone",
    required: ["name"],
    properties: {
      name: { type: "string", title: "Name", format: "iana-timezone" },
      hours: {
        type: "array",
        title: "Hours",
        default: [9, 18],
        minItems: 2,
        maxItems: 2,
        prefixItems: [
          { type: "integer", minimum: 0, maximum: 24 },
          { type: "integer", minimum: 0, maximum: 24 },
        ],
      },
    },
  },
};

const report: SchemaConfigReport = {
  ...config,
  files: [
    { scope: "site", path: "/etc/hz/hz.toml", exists: false },
    { scope: "user", path: "/home/mira/.config/hz/hz.toml", exists: true },
  ],
  settings: [
    ...config.settings,
    {
      section: "github",
      key: "api_url",
      value: "https://api.github.com",
      default: "https://api.github.com",
      source: "default",
      env: "HZ_GITHUB__API_URL",
      description: "Where the GitHub API is.",
    },
    {
      section: "capabilities",
      key: "tracker",
      value: "github",
      default: null,
      source: "user",
      env: "HZ_CAPABILITIES__TRACKER",
      description: "Adapter that serves `tracker`.",
    },
    {
      section: "clock",
      key: "zones",
      value: [{ name: "Europe/Berlin", hours: [9, 18] }, "America/New_York"],
      default: [],
      source: "user",
      env: "HZ_CLOCK__ZONES",
      description: "The team's time zones.",
      json_schema: zonesSchema,
    },
    {
      section: "clock",
      key: "working_hours",
      value: [8, 16],
      default: null,
      source: "user",
      env: "HZ_CLOCK__WORKING_HOURS",
      description: "Deprecated: the hours of every zone given by name only.",
      json_schema: { anyOf: [{ type: "array" }, { type: "null" }], deprecated: true },
    },
  ],
};

const answers = {
  ...shell,
  "/api/system/config/show": report,
  "/api/system/plugin/list": [github, clock],
  "/api/system/capability/list": {
    capabilities: [{ name: "tracker", summary: "Issues", modes: ["read"], bound: "github" }],
    adapters: [],
    uses: [],
  },
  "/api/system/config/set": "/home/mira/.config/hz/hz.toml",
  "/api/system/config/reveal": {
    scope: "user",
    path: "/home/mira/.config/hz/hz.toml",
    supported: true,
    opened: true,
    message: "Opened",
  },
};

/** The `value` of the last `config set`. */
const lastSet = (calls: Request[]) =>
  new URL(calls.filter((call) => call.url.includes("/system/config/set")).at(-1)?.url ?? "http://x").searchParams.get(
    "value",
  );

describe("Settings: Preferences, Customize, This computer", () => {
  it("lists every module with settings under Preferences, and connectors and plugins under Customize", async () => {
    mockApi(answers);
    const { container } = renderAt("/doctor?settings=hz");
    const nav = await screen.findByRole("navigation", { name: "Settings" });
    for (const name of [
      "Appearance",
      "World Clock",
      "Notifications",
      "AI",
      "Connectors",
      "Plugins",
      "Skills",
      "You",
      "Team",
      "Daemon",
      "Configuration files",
      "From Claude",
      "Updates",
      "System",
    ]) {
      expect(await within(nav).findByRole("button", { name })).toBeInTheDocument();
    }
    await waitFor(() => expect(within(nav).queryByRole("button", { name: /github/i })).not.toBeInTheDocument());
    const names = within(nav)
      .getAllByRole("button")
      .map((button) => button.textContent);
    expect(names.slice(0, 3)).toEqual(["Appearance", "World Clock", "Notifications"]);
    // Every entry has its own icon, never a shared generic one, and AI is only under Customize.
    expect(names.filter((name) => name === "AI")).toHaveLength(1);
    expect(within(nav).queryByRole("button", { name: "General" })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("button", { name: /weather/i })).not.toBeInTheDocument();
    expect(
      within(nav)
        .getAllByRole("button")
        .every((button) => button.querySelector("svg")),
    ).toBe(true);
    // Each entry has an icon of its own: only Plugins wears the puzzle piece.
    expect(new Set(PAGES.map((page) => page.icon)).size).toBe(PAGES.length);
    // Connectors have their settings on their cards; the [capabilities] table is there too.
    expect(within(nav).queryByRole("button", { name: "Capabilities" })).not.toBeInTheDocument();
    await expectAccessible(container);
  });

  it("heads a plugin module's settings with where they come from, as its row under Plugins does", async () => {
    const sourced = {
      ...clock,
      summary: "What time it is for everyone on the team: a Heinzelmännchen plugin.",
      source_url: "https://github.com/helmut-hoffer-von-ankershoffen/heinzel/tree/v0.3.0/plugins/heinzel-clock",
    };
    mockApi({
      ...answers,
      "/api/system/plugin/list": [github, sourced],
      "/api/system/plugin/search": { plugins: [], warnings: [] },
    });
    const user = userEvent.setup();
    const { container } = renderAt("/doctor?settings=clock");
    const nav = await screen.findByRole("navigation", { name: "Settings" });
    expect(await within(nav).findByRole("button", { name: "World Clock" })).toHaveAttribute("aria-current", "page");
    const plugin = await screen.findByTitle("Show it under Plugins");
    const header = (plugin.closest("div") as HTMLElement).parentElement as HTMLElement;
    expect(within(header).getByTitle("Capability: hz clock")).toBeInTheDocument();
    expect(header).toHaveTextContent("First-party");
    expect(header).toHaveTextContent("v0.3.0");
    expect(
      within(header).getByRole("link", { name: "github.com/helmut-hoffer-von-ankershoffen/heinzel" }),
    ).toBeInTheDocument();
    expect(screen.getByText("What time it is for everyone on the team.")).toBeInTheDocument();
    await expectAccessible(container);
    await user.click(plugin);
    expect(within(nav).getByRole("button", { name: "Plugins" })).toHaveAttribute("aria-current", "page");
  });

  it("heads a built-in module's settings with hz and its version", async () => {
    mockApi({ ...answers, "/api/system/info": { version: "0.4.0", modules: [] } });
    renderAt("/doctor?settings=org");
    expect(await screen.findByRole("link", { name: "First-party" })).toHaveAttribute(
      "href",
      "https://github.com/helmut-hoffer-von-ankershoffen/heinzel",
    );
    const dialog = screen.getByRole("dialog", { name: "Settings" });
    expect(within(dialog).getByText("v0.4.0")).toBeInTheDocument();
  });

  it("lists connectors as cards with what they connect, and opens one's settings inside Connectors", async () => {
    mockApi(answers);
    const user = userEvent.setup();
    renderAt("/doctor?settings=connectors");
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    const card = (await within(dialog).findByText("Connects: code-host, tracker")).closest("button") as HTMLElement;
    expect(card).toHaveTextContent("Connects: code-host, tracker");
    expect(await within(card).findByText("In use for tracker")).toBeInTheDocument();
    expect(within(dialog).getByRole("heading", { name: "Which connector serves what" })).toBeInTheDocument();
    await user.click(card);
    expect(await within(dialog).findByRole("heading", { name: "GitHub" })).toBeInTheDocument();
    expect(within(dialog).getByLabelText("API URL")).toHaveValue("https://api.github.com");
    const nav = within(dialog).getByRole("navigation", { name: "Settings" });
    expect(within(nav).getByRole("button", { name: "Connectors" })).toHaveAttribute("aria-current", "page");
  });

  it("finds a setting of any part by search", async () => {
    mockApi(answers);
    const user = userEvent.setup();
    renderAt("/doctor?settings=hz");
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    await user.type(await within(dialog).findByRole("searchbox"), "api_url");
    expect(await within(dialog).findByLabelText("API URL")).toBeInTheDocument();
  });

  it("shows a configuration file in Finder through hz system config reveal", async () => {
    const { calls } = mockApi(answers);
    const user = userEvent.setup();
    renderAt("/doctor?settings=files");
    await user.click(await screen.findByRole("button", { name: "Show in Finder: user" }));
    await waitFor(() => expect(calls.some((call) => call.url.includes("/system/config/reveal"))).toBe(true));
    const reveal = calls.find((call) => call.url.includes("/system/config/reveal"));
    expect(reveal?.method).toBe("POST");
    expect(new URL(reveal?.url ?? "").searchParams.get("scope")).toBe("user");
  });

  it("offers no reveal button where hz can't open a file manager", async () => {
    mockApi({ ...answers, "/api/system/config/show": { ...report, can_reveal: false, file_manager: "file manager" } });
    renderAt("/doctor?settings=files");
    await screen.findByRole("heading", { name: "Configuration files" });
    expect(screen.queryByRole("button", { name: /Show in Finder|Open folder/ })).not.toBeInTheDocument();
  });
});

describe("The time zones editor (a list of tables by its schema)", () => {
  it("shows each zone as a row, adds the browser's zone with 9–18, changes hours and removes a row", async () => {
    const { calls } = mockApi(answers);
    const user = userEvent.setup();
    const { container } = renderAt("/doctor?settings=clock");
    const zones = await screen.findByRole("group", { name: "Zones" });
    expect(
      within(zones).getByRole("button", { name: /^Name \(Berlin\): Berlin · Europe\/Berlin/ }),
    ).toBeInTheDocument();
    // A plain name is a zone with the default hours.
    expect(within(zones).getByRole("combobox", { name: "Hours (New York) from" })).toHaveValue("9");
    await expectAccessible(container);

    await user.selectOptions(within(zones).getByRole("combobox", { name: "Hours (Berlin) to" }), "17");
    await waitFor(() =>
      expect(lastSet(calls)).toBe('[{ name = "Europe/Berlin", hours = [9, 17] }, { name = "America/New_York" }]'),
    );

    await user.click(within(zones).getByRole("button", { name: "Remove New York" }));
    await waitFor(() => expect(lastSet(calls)).toBe('[{ name = "Europe/Berlin", hours = [9, 18] }]'));

    await user.click(within(zones).getByRole("button", { name: "Add to Zones" }));
    const own = Intl.DateTimeFormat().resolvedOptions().timeZone;
    await waitFor(() => expect(lastSet(calls)).toContain(`{ name = "${own}", hours = [9, 18] }`));
  });

  it("never shows a deprecated setting, even one that is set, nor finds it by search", async () => {
    mockApi(answers);
    const user = userEvent.setup();
    renderAt("/doctor?settings=clock");
    await screen.findByRole("group", { name: "Zones" });
    expect(screen.queryByText("Working hours")).not.toBeInTheDocument();
    await user.type(screen.getByRole("searchbox"), "working_hours");
    expect(await screen.findByText("No setting matches.")).toBeInTheDocument();
  });

  it("marks the first zone as yours, reorders rows, and puts your own zone on top", async () => {
    const { calls } = mockApi(answers);
    const user = userEvent.setup();
    renderAt("/doctor?settings=clock");
    const zones = await screen.findByRole("group", { name: "Zones" });
    const [berlin, newYork] = within(zones).getAllByRole("listitem");
    expect(within(berlin as HTMLElement).getByText("Primary")).toBeInTheDocument();
    expect(within(newYork as HTMLElement).queryByText("Primary")).not.toBeInTheDocument();
    expect(within(zones).getByRole("button", { name: "Move Berlin up" })).toBeDisabled();
    expect(within(zones).getByRole("button", { name: "Move New York down" })).toBeDisabled();
    await user.click(within(zones).getByRole("button", { name: "Move New York up" }));
    await waitFor(() =>
      expect(lastSet(calls)).toBe('[{ name = "America/New_York" }, { name = "Europe/Berlin", hours = [9, 18] }]'),
    );
  });

  it("offers your own time zone when it isn't in the list, at the top", async () => {
    const own = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const other = own === "Asia/Kolkata" ? "Asia/Tokyo" : "Asia/Kolkata";
    const zones = report.settings.map((setting) =>
      setting.key === "zones" ? { ...setting, value: [{ name: other, hours: [9, 18] }] } : setting,
    );
    const { calls } = mockApi({ ...answers, "/api/system/config/show": { ...report, settings: zones } });
    const user = userEvent.setup();
    renderAt("/doctor?settings=clock");
    await user.click(await screen.findByRole("button", { name: /^Use my time zone/ }));
    await waitFor(() =>
      expect(lastSet(calls)).toBe(`[{ name = "${own}", hours = [9, 18] }, { name = "${other}", hours = [9, 18] }]`),
    );
  });

  it("picks a time zone by searching", async () => {
    const { calls } = mockApi(answers);
    const user = userEvent.setup();
    renderAt("/doctor?settings=clock");
    const zones = await screen.findByRole("group", { name: "Zones" });
    await user.click(within(zones).getByRole("button", { name: /^Name \(Berlin\)/ }));
    await user.type(screen.getByRole("combobox", { name: "Search time zones" }), "tokyo");
    await user.click(screen.getByRole("option", { name: /Tokyo/ }));
    await waitFor(() => expect(lastSet(calls)).toContain('name = "Asia/Tokyo", hours = [9, 18]'));
  });

  it("shows the server's error when a change is refused", async () => {
    mockApi({
      ...answers,
      "/api/system/config/set": { status: 400, body: { error: "Invalid value for clock.zones: end before start" } },
    });
    const user = userEvent.setup();
    renderAt("/doctor?settings=clock");
    const zones = await screen.findByRole("group", { name: "Zones" });
    await user.selectOptions(within(zones).getByRole("combobox", { name: "Hours (Berlin) to" }), "3");
    expect(await screen.findByText(/end before start/)).toHaveAttribute("role", "alert");
  });
});

describe("Installed plugins by kind", () => {
  const acme = plugin({
    name: "acme",
    summary: "Acme, a made-up company.",
    themes: ["acme"],
    playbooks: ["engineering-team"],
    kinds: ["theme", "playbook"],
  });

  it("badges what each plugin brings, says what exactly, and filters by kind", async () => {
    mockApi({
      ...answers,
      "/api/system/plugin/list": [github, clock, acme],
      "/api/system/plugin/search": { plugins: [], warnings: [] },
    });
    const user = userEvent.setup();
    const { container } = renderAt("/doctor?settings=plugins");
    const row = (await screen.findByText("GitHub", { selector: "span *, span" })).closest("li") as HTMLElement;
    expect(within(row).getByTitle("Connector: code-host, tracker")).toBeInTheDocument();
    const acmeRow = screen.getByText("Acme", { selector: "li span" }).closest("li") as HTMLElement;
    expect(within(acmeRow).getByTitle("Theme: acme")).toBeInTheDocument();
    expect(within(acmeRow).getByTitle("Playbook: engineering-team")).toBeInTheDocument();
    await expectAccessible(container);
    await user.click(screen.getByRole("button", { name: "Theme", pressed: false }));
    expect(screen.queryByTitle("Connector: code-host, tracker")).not.toBeInTheDocument();
    expect(screen.getByTitle("Theme: acme")).toBeInTheDocument();
  });
});

describe("Where settings live", () => {
  it("keeps the capitals of acronyms in module and setting names", () => {
    expect(displayName("ai")).toBe("AI");
    expect(displayName("clock", "World Clock: what time it is.")).toBe("World Clock");
    expect(displayName("mcp")).toBe("MCP");
    expect(displayName("cli")).toBe("CLI");
    expect(displayName("github")).toBe("GitHub");
    expect(displayName("ai-access")).toBe("AI access");
    expect(inWords("ui_port")).toBe("UI port");
    expect(inWords("api_allowed_hosts")).toBe("API allowed hosts");
    expect(inWords("weekly_digest")).toBe("Weekly digest");
  });

  it("names plugins as their authors write them", () => {
    expect(displayName("github", github.summary)).toBe("GitHub");
    expect(displayName("atlassian", "Read Jira issues")).toBe("Atlassian");
    expect(displayName("google", "Read Google Docs")).toBe("Google");
  });

  it("puts every module with settings under Preferences, General first, and connectors apart", () => {
    const nav = settingsNav(["hz", "org", "github", "clock", "capabilities", "acme"], [github, clock]);
    const ids = nav.pages.map((entry) => entry.id);
    expect(ids.slice(0, 3)).toEqual(["appearance", "clock", "notifications"]);
    expect(ids).toContain("acme");
    expect(ids).not.toContain("github");
    expect(nav.pages.find((entry) => entry.id === "clock")?.plugin).toBe(clock);
    // Each setting section has exactly one page.
    for (const [section, page] of [
      ["system.runner.chat", "ai"],
      ["ai.access", "ai"],
      ["system.sound", "appearance"],
      ["system.notify", "notifications"],
      ["org", "you"],
      ["system.daemon", "daemon"],
      ["system.directory", "system"],
    ] as const)
      expect(pageOf(section, nav.pages)?.id).toBe(page);
    expect(nav.connectors.map((entry) => entry.title)).toEqual(["GitHub"]);
  });

  it("writes values as TOML `hz system config set` reads", () => {
    expect(toToml([{ name: "Asia/Tokyo", hours: [10, 19] }])).toBe('[{ name = "Asia/Tokyo", hours = [10, 19] }]');
    expect(toToml(["a", 'b"c'])).toBe('["a", "b\\"c"]');
    expect(toToml({ "odd key": true })).toBe('{ "odd key" = true }');
    expect(toToml([1, null, Number.NaN, Number.NEGATIVE_INFINITY])).toBe("[1, nan, -inf]");
    expect(toToml("a\x7fb")).toBe('"a\\u007fb"');
  });
});

describe("Settings: one place for everything", () => {
  const list = [
    {
      id: "clock:primary-zone",
      module: "clock",
      kind: "status",
      title: "Your time zone",
      slots: { footer: true, sidebar: false },
      defaults: { footer: true, sidebar: false },
    },
  ];
  const entry = { module: "clock", plugin: "heinzel-clock", order: 900, refresh: 60, problem: null };
  const ui = {
    ...answers,
    "/api/system/ui/list": list,
    "/api/system/ui/show": list,
    "/api/system/ui/hide": list,
    "/api/system/ui/status": [
      {
        ...entry,
        id: "clock:primary-zone",
        slots: { footer: true, sidebar: false },
        item: { text: "Berlin", tone: "neutral", icon: "globe", live: { kind: "clock", zone: "Europe/Berlin" } },
      },
    ],
  };

  it("keeps AI access under AI alone, and appearance under Appearance", async () => {
    mockApi(ui);
    const ai = renderAt("/doctor?settings=ai");
    expect(await screen.findByText("AI access")).toBeInTheDocument();
    ai.unmount();
    renderAt("/doctor?settings=appearance");
    expect(await screen.findByRole("radiogroup", { name: "Mode" })).toBeInTheDocument();
    expect(screen.queryByText("AI access")).not.toBeInTheDocument();
  });

  it("shows each placement of World Clock in plain words with a live preview and a switch", async () => {
    const { calls } = mockApi(ui);
    const user = userEvent.setup();
    const { container } = renderAt("/doctor?settings=clock");
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    expect(await within(dialog).findByText("In the footer")).toBeInTheDocument();
    expect(within(dialog).getByText("In the sidebar")).toBeInTheDocument();
    const footer = container.querySelector('[data-preview="footer"]') as HTMLElement;
    await waitFor(() => expect(footer).toHaveTextContent(/Berlin \d{2}:\d{2}:\d{2}/));
    expect(container.querySelector('[data-preview="sidebar"]')).toHaveClass("opacity-40");
    await expectAccessible(container);
    await user.click(within(dialog).getByRole("switch", { name: "Show in sidebar: Your time zone" }));
    await waitFor(() => expect(calls.some((call) => call.url.includes("/system/ui/show"))).toBe(true));
  });

  it("finds a page by what is on it, and every setting by its name", async () => {
    mockApi(ui);
    const user = userEvent.setup();
    renderAt("/doctor?settings=");
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    await user.type(await within(dialog).findByRole("searchbox"), "volume");
    const pages = await within(dialog).findByRole("region", { name: "Settings pages" });
    await user.click(within(pages).getByRole("button", { name: "Appearance" }));
    expect(await within(dialog).findByRole("radiogroup", { name: "Mode" })).toBeInTheDocument();
  });

  it("opens old links where their setting moved", async () => {
    mockApi(ui);
    renderAt("/doctor?settings=org");
    const nav = await screen.findByRole("navigation", { name: "Settings" });
    expect(await within(nav).findByRole("button", { name: "You" })).toHaveAttribute("aria-current", "page");
  });
});
