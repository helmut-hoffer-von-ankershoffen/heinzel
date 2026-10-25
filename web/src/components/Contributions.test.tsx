import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { ICONS, liveText, widgetText } from "./Contributions";

const entry = { module: "acme", plugin: "heinzel-acme", order: 500, refresh: 60, problem: null };

const status = [
  {
    ...entry,
    id: "acme:reviews",
    slots: { footer: true },
    item: {
      text: "3 reviews",
      tone: "warn",
      icon: "git-pull-request",
      detail: [{ text: "Oldest: 4 days", tone: "bad" }],
      actions: [
        {
          label: "Sync",
          effect: {
            kind: "operation",
            operation: "sync",
            arguments: {},
            call: {
              method: "POST",
              path: "/acme/sync",
              query: { dry_run: true },
              command: "hz acme sync --dry-run true",
              summary: "Sync the reviews.",
              read_only: false,
            },
          },
        },
      ],
    },
  },
  { ...entry, id: "acme:broken", slots: { footer: true }, item: null, problem: "acme:broken failed: RuntimeError" },
  {
    ...entry,
    id: "acme:zone",
    slots: { footer: false, sidebar: true },
    item: { text: "Lisbon", tone: "neutral", icon: "clock", live: { kind: "clock", zone: "Europe/Lisbon" } },
  },
];

const actions = [
  {
    ...entry,
    id: "acme:team",
    slots: { header: true },
    label: "Team clock",
    icon: "globe",
    badge: 2,
    effect: { kind: "command", command: "hz clock", runs: true },
  },
  { ...entry, id: "acme:hidden", slots: { header: false }, label: "Hidden", icon: "zap", badge: 0, effect: null },
];

const widgets = [
  {
    ...entry,
    id: "acme:at-work",
    slots: { today: true },
    refresh: 300,
    widget: { kind: "metric", label: "Zones at work now", value: "2/3", tone: "ok" },
  },
  {
    ...entry,
    id: "acme:commute",
    slots: { today: true },
    refresh: 300,
    widget: {
      kind: "graphic",
      label: "Commute home",
      value: "12 min",
      caption: "Light traffic",
      avatar: { accessories: ["sunglasses", "scarf"] },
    },
  },
  {
    ...entry,
    id: "acme:wide",
    slots: { today: true },
    refresh: 300,
    widget: { kind: "card", title: "Roadmap", body: "Three things", size: "wide" },
  },
];

const answers = {
  ...shell,
  "/api/system/ui/status": status,
  "/api/system/ui/actions": actions,
  "/api/system/ui/widgets": widgets,
  "/api/acme/sync": 1,
};

describe("Contributions", () => {
  it("shows each module's footer and sidebar items; a failing one is only a mark with its problem", async () => {
    mockApi(answers);
    const { container } = renderAt("/doctor");
    const footer = await screen.findByText("3 reviews");
    expect(footer.closest("[data-slot]")).toHaveAttribute("data-slot", "footer");
    expect(screen.getByLabelText("acme:broken failed: RuntimeError")).toBeInTheDocument();
    // In the sidebar only: its footer slot is off.
    const zone = await screen.findByText(/^Lisbon \d\d:\d\d:\d\d/);
    expect(zone.closest("[data-slot]")).toHaveAttribute("data-slot", "sidebar");
    await expectAccessible(container);
  });

  it("asks before a write and runs exactly the call the server built", async () => {
    const { calls } = mockApi(answers);
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.hover(await screen.findByText("3 reviews"));
    await user.click(await screen.findByRole("button", { name: "Sync" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Sync the reviews." });
    expect(within(dialog).getByText("hz acme sync --dry-run true")).toBeInTheDocument();
    expect(calls.some((call) => new URL(call.url).pathname === "/api/acme/sync")).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Run it" }));
    await waitFor(() => {
      const sync = calls.find((call) => new URL(call.url).pathname === "/api/acme/sync");
      expect(sync?.method).toBe("POST");
      expect(new URL(sync?.url ?? "http://x").searchParams.get("dry_run")).toBe("true");
    });
  });

  it("adds header buttons with their badge, leaving out hidden ones", async () => {
    mockApi(answers);
    renderAt("/doctor");
    expect(await screen.findByRole("button", { name: "Team clock (2)" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Hidden/ })).not.toBeInTheDocument();
  });

  it("puts widgets on Today, with Ask about it and dismiss", async () => {
    mockApi(answers);
    const user = userEvent.setup();
    renderAt("/");
    const card = (await screen.findByText("2/3")).closest("li") as HTMLElement;
    expect(within(card).getByText("Zones at work now")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Dismiss Zones at work now" }));
    expect(screen.queryByText("2/3")).not.toBeInTheDocument();
  });

  it("shows one card per contribution id, however often the list repeats it", async () => {
    mockApi({ ...answers, "/api/system/ui/widgets": [...widgets, ...widgets] });
    renderAt("/");
    await screen.findByText("2/3");
    expect(screen.getAllByText("2/3")).toHaveLength(1);
    expect(document.querySelectorAll('[data-widget="acme:at-work"]')).toHaveLength(1);
  });

  it("lays widgets out by the size they ask for, or their kind's default", async () => {
    mockApi(answers);
    renderAt("/");
    const sizeOf = async (text: string) => ((await screen.findByText(text)).closest("li") as HTMLElement).dataset.size;
    expect(await sizeOf("2/3")).toBe("small");
    expect(await sizeOf("Roadmap")).toBe("wide");
    expect(await sizeOf("12 min")).toBe("medium");
  });

  it("draws a graphic widget: a big value beside a Heinzelmännchen dressed by data", async () => {
    mockApi(answers);
    renderAt("/");
    const card = (await screen.findByText("12 min")).closest("li") as HTMLElement;
    expect(within(card).getByText("Light traffic")).toBeInTheDocument();
    expect(within(card).getByRole("img", { name: /Your Heinzelmännchen.*sunglasses.*scarf/ })).toBeInTheDocument();
    await expectAccessible(card.parentElement as HTMLElement);
  });
});

describe("Contribution toggles", () => {
  it("shows a switch per slot on the module's settings page, and a click writes the choice", async () => {
    const list = [
      {
        id: "org:meeting",
        module: "org",
        kind: "status",
        title: "Meeting",
        slots: { footer: true, sidebar: false },
        defaults: { footer: true, sidebar: false },
      },
    ];
    const { calls } = mockApi({ ...answers, "/api/system/ui/list": list, "/api/system/ui/show": list });
    const user = userEvent.setup();
    renderAt("/doctor?settings=org");
    const sidebar = await screen.findByRole("switch", { name: "Show in sidebar: Meeting" });
    expect(sidebar).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("switch", { name: "Show in footer: Meeting" })).toHaveAttribute("aria-checked", "true");
    await user.click(sidebar);
    await waitFor(() => {
      const show = calls.find((call) => new URL(call.url).pathname === "/api/system/ui/show");
      expect(show?.method).toBe("POST");
      expect(new URL(show?.url ?? "http://x").searchParams.get("item")).toBe("org:meeting");
      expect(new URL(show?.url ?? "http://x").searchParams.get("slot")).toBe("sidebar");
    });
  });
});

describe("Contribution data", () => {
  it("maps every icon and renders live values", () => {
    expect(Object.keys(ICONS)).toHaveLength(30);
    const now = new Date("2026-10-26T10:00:00Z");
    expect(liveText({ kind: "until", at: "2026-10-26T10:12:00Z" }, now)).toBe("in 12 min");
    expect(liveText({ kind: "until", at: "2026-10-26T09:59:59Z" }, now)).toBe("now");
    expect(liveText({ kind: "clock", zone: "UTC" }, now)).toBe("10:00:00");
    expect(liveText({ kind: "clock", zone: "America/New_York" }, new Date("2026-10-26T20:45:00Z"))).toBe("16:45:00");
    expect(liveText({ kind: "time", at: "2026-10-26T20:45:00Z" }, now)).toMatch(/^\d{2}:\d{2}$/);
    expect(
      widgetText({
        kind: "list",
        title: "Reviews",
        items: [
          { text: "a", tone: "neutral" },
          { text: "b", tone: "ok" },
        ],
      }),
    ).toBe("Reviews: a; b");
    expect(widgetText({ kind: "progress", label: "Release", done: 2, total: 5, tone: "neutral" })).toBe(
      "Release: 2 of 5",
    );
  });
});
