import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { SchemaConfigReport, SchemaPluginInfo } from "@/api/client";
import { config, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { toToml } from "./settingSchema";
import { displayName, settingsNav } from "./settingsSections";

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
const clock = plugin({ name: "clock", summary: "What time it is for everyone on the team.", kinds: ["capability"] });

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
    for (const name of ["General", "Clock", "Org", "System", "Connectors", "Plugins", "Configuration files"]) {
      expect(await within(nav).findByRole("button", { name })).toBeInTheDocument();
    }
    await waitFor(() => expect(within(nav).queryByRole("button", { name: /github/i })).not.toBeInTheDocument());
    const names = within(nav)
      .getAllByRole("button")
      .map((button) => button.textContent);
    expect(names.slice(0, 4)).toEqual(["General", "Clock", "Org", "System"]);
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
    expect(await within(nav).findByRole("button", { name: "Clock" })).toHaveAttribute("aria-current", "page");
    const plugin = await screen.findByTitle("Show it under Plugins");
    const header = plugin.closest("div") as HTMLElement;
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
    expect(await screen.findByRole("link", { name: "Built in · heinzel v0.4.0" })).toHaveAttribute(
      "href",
      "https://github.com/helmut-hoffer-von-ankershoffen/heinzel",
    );
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
    expect(within(dialog).getByLabelText("Api url")).toHaveValue("https://api.github.com");
    const nav = within(dialog).getByRole("navigation", { name: "Settings" });
    expect(within(nav).getByRole("button", { name: "Connectors" })).toHaveAttribute("aria-current", "page");
  });

  it("finds a setting of any part by search", async () => {
    mockApi(answers);
    const user = userEvent.setup();
    renderAt("/doctor?settings=hz");
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    await user.type(await within(dialog).findByRole("searchbox"), "api_url");
    expect(await within(dialog).findByLabelText("Api url")).toBeInTheDocument();
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
  it("names plugins as their authors write them", () => {
    expect(displayName("github", github.summary)).toBe("GitHub");
    expect(displayName("atlassian", "Read Jira issues")).toBe("Atlassian");
    expect(displayName("google", "Read Google Docs")).toBe("Google");
  });

  it("puts every module with settings under Preferences, General first, and connectors apart", () => {
    const nav = settingsNav(["hz", "org", "github", "clock", "capabilities"], [github, clock]);
    expect(nav.preferences.map((entry) => entry.id)).toEqual(["hz", "clock", "org"]);
    expect(nav.preferences.find((entry) => entry.id === "clock")?.plugin).toBe(clock);
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
