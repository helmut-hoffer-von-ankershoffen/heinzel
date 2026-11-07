import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

const answers = {
  ...shell,
  "/api/system/info": { version: "0.4.0", modules: [{ qualname: "clock" }] },
  "/api/clock": [
    { zone: "Europe/Berlin", time: "Fri 17:04", hours: [9, 18], working: true },
    { zone: "America/New_York", time: "Fri 11:04", hours: [9, 18], working: true },
  ],
};

describe("Mounted footer measurements", () => {
  afterEach(() => vi.restoreAllMocks());
  it("places the last contributed status after native controls and overflow in DOM and keyboard order", async () => {
    mockApi({
      ...answers,
      "/api/system/ui/status": [
        {
          id: "external:first",
          module: "external",
          plugin: "example-provider",
          order: 10,
          refresh: 60,
          problem: null,
          slots: { footer: true },
          item: {
            text: "First generic status",
            tone: "neutral",
            icon: "bot",
            detail: [{ text: "First", tone: "neutral" }],
          },
        },
        {
          id: "external:last",
          module: "external",
          plugin: "example-provider",
          order: 900,
          refresh: 60,
          problem: null,
          slots: { footer: true },
          item: {
            text: "Last generic status",
            tone: "neutral",
            icon: "clock",
            detail: [{ text: "Last", tone: "neutral" }],
          },
        },
      ],
      "/api/heinzel/sessions": { sessions: [] },
    });
    renderAt("/doctor");
    const footer = await screen.findByRole("contentinfo");
    const last = await within(footer).findByRole("button", { name: "Last generic status, off" });
    const buttons = within(footer).getAllByRole("button");
    expect(buttons.at(-1)).toBe(last);
    expect(buttons.indexOf(last)).toBeGreaterThan(
      buttons.indexOf(within(footer).getByRole("button", { name: /System health/ })),
    );
  });
  it("shows generic meter details without repeating their summary above the pane bullets", async () => {
    mockApi({
      ...answers,
      "/api/system/ui/status": [
        {
          id: "external:resources",
          module: "external",
          plugin: "example-provider",
          order: 10,
          refresh: 60,
          problem: null,
          slots: { footer: true },
          item: {
            text: "Computer resources (CPU 12%, Memory 35%, Load scaled 8%)",
            tone: "neutral",
            meters: [
              { label: "CPU", value: 12, tone: "ok" },
              { label: "Memory", value: 35, tone: "ok" },
              { label: "Load scaled", value: 8, tone: "ok" },
            ],
            detail: [
              { text: "CPU: 12%", tone: "ok" },
              { text: "Memory: 35%", tone: "ok" },
              { text: "Load scaled: 8%", tone: "ok" },
            ],
          },
        },
      ],
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    const footer = await screen.findByRole("contentinfo");
    const trigger = await within(footer).findByRole("button", {
      name: "Computer resources (CPU 12%, Memory 35%, Load scaled 8%); CPU: 12%; Memory: 35%; Load scaled: 8%, off",
    });
    await user.click(trigger);
    const pane = await screen.findByRole("region", {
      name: "Computer resources (CPU 12%, Memory 35%, Load scaled 8%)",
    });
    expect(
      within(pane).queryByText("Computer resources (CPU 12%, Memory 35%, Load scaled 8%)", { selector: "p" }),
    ).not.toBeInTheDocument();
    expect(within(pane).getByText("CPU: 12%")).toBeInTheDocument();
    expect(within(pane).getByText("Memory: 35%")).toBeInTheDocument();
    expect(within(pane).getByText("Load scaled: 8%")).toBeInTheDocument();
  });
  it("keeps the mounted durable Heinzel control visible alongside resources and health at 375px", async () => {
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
      const id = this.getAttribute("data-footer-content");
      const widths: Record<string, number> = {
        "@version": 56,
        "@resources": 120,
        "@heinzels": 24,
        "@health": 24,
        "@clock": 80,
        "@meeting": 24,
        "@more": 24,
      };
      let width = this.tagName === "FOOTER" ? 375 : id ? (widths[id] ?? 24) : 0;
      if (id === "@credit")
        width =
          this.querySelector<HTMLElement>("[data-footer-credit-text]")?.parentElement?.style.width === "0px" ? 0 : 150;
      if (this.hasAttribute("data-footer-credit-text")) width = 150;
      return new DOMRect(0, 0, width, 24);
    });
    mockApi({
      ...answers,
      "/api/system/ui/status": [
        {
          id: "system:daemon",
          module: "system",
          plugin: null,
          order: 1,
          refresh: 60,
          problem: null,
          slots: { footer: true },
          item: { text: "Daemon running", tone: "ok", icon: "bot" },
        },
      ],
      "/api/heinzel/sessions": { sessions: [] },
    });
    renderAt("/doctor");
    const footer = await screen.findByRole("contentinfo");
    expect(await within(footer).findByRole("button", { name: "Daemon running, OK" })).toBeInTheDocument();
    expect(within(footer).getByRole("button", { name: /System health/ })).toBeInTheDocument();
    expect(within(footer).getByRole("button", { name: "0 Heinzelmännchen, off" })).toBeInTheDocument();
    expect(footer.textContent).not.toContain("🐻");
    expect(footer.querySelector('[data-footer-part="@version"]')).toBe(footer.firstElementChild);
  });
});

describe("Footer items are icons with a state and a pane", () => {
  const entry = { module: "system", plugin: null, order: 1, refresh: 60, problem: null };
  const item = (id: string, tone: "ok" | "warn" | "bad" | "neutral") => ({
    ...entry,
    id,
    slots: { footer: true },
    item: {
      text: `Thing ${tone}`,
      tone,
      icon: "bot",
      detail: [{ text: "Jobs: 3", tone: "neutral" }],
      actions: [{ label: "Logs", icon: "file-text", effect: { kind: "command", command: "hz system daemon logs" } }],
    },
  });
  const states = {
    ...answers,
    "/api/system/ui/status": [
      item("a:ok", "ok"),
      item("a:warn", "warn"),
      item("a:bad", "bad"),
      item("a:off", "neutral"),
    ],
    "/api/org/agenda": { current: null, next: null, problem: "no calendar", fix: null },
    "/api/org/meeting": null,
    "/api/system/changelog": "## [0.4.0] - 2026-10-25\n\n### Features\n\n- Something new\n",
  };
  const widthBefore = window.innerWidth;
  afterEach(() => {
    window.innerWidth = widthBefore;
  });

  it("gives each state its own colour and accessible state, and passes axe", async () => {
    mockApi(states);
    window.innerWidth = 1280;
    const { container } = renderAt("/doctor");
    const bar = (await screen.findByRole("contentinfo")) as HTMLElement;
    expect(within(bar).queryByText("Heinzels accelerating product teams")).not.toBeInTheDocument();
    expect(within(bar).queryByText("built with AI in Berlin")).not.toBeInTheDocument();
    for (const [tone, word, colour] of [
      ["ok", "OK", "text-ok"],
      ["warn", "warning", "text-warn"],
      ["bad", "error", "text-bad"],
      ["off", "off", "text-muted-foreground"],
    ] as const) {
      const button = await within(bar).findByRole("button", {
        name: `Thing ${tone === "off" ? "neutral" : tone}, ${word}`,
      });
      expect(button).toHaveClass(colour);
      expect(button.parentElement).toHaveAttribute("data-state", tone);
      expect(button.querySelectorAll("svg")).toHaveLength(1); // no secondary state glyph beside the footer icon
    }
    expect(bar.textContent).not.toContain("_"); // no literal underscore badges beside footer icons
    await expectAccessible(container);
  });

  it("offers the safe terminal install action only while the daemon is not installed", async () => {
    const notInstalled = {
      ...answers,
      "/api/system/ui/status": [
        {
          id: "system:daemon",
          module: "system",
          plugin: null,
          order: 700,
          refresh: 60,
          problem: null,
          slots: { footer: true },
          item: {
            text: "Daemon not installed",
            tone: "neutral",
            icon: "bot",
            actions: [
              { label: "Install", icon: "play", effect: { kind: "command", command: "hz system daemon install" } },
            ],
          },
        },
      ],
    };
    mockApi(notInstalled);
    const user = userEvent.setup();
    const notInstalledView = renderAt("/doctor");
    const footer = await screen.findByRole("contentinfo");
    const daemon = await within(footer).findByRole("button", { name: "Daemon not installed, off" });
    await user.click(daemon);
    expect(await screen.findByRole("button", { name: "Install" })).toBeInTheDocument();
    notInstalledView.unmount();

    const installed = {
      ...notInstalled,
      "/api/system/ui/status": [
        {
          ...notInstalled["/api/system/ui/status"][0],
          item: { text: "Daemon running", tone: "ok", icon: "bot", actions: [] },
        },
      ],
    };
    mockApi(installed);
    renderAt("/doctor");
    const installedDaemon = await within(await screen.findByRole("contentinfo")).findByRole("button", {
      name: "Daemon running, OK",
    });
    await user.click(installedDaemon);
    expect(screen.queryByRole("button", { name: "Install" })).not.toBeInTheDocument();
  });

  it("opens the pane on hover and focus, closes it on Escape and on blur, and pins it on click, by keyboard too", async () => {
    mockApi(states);
    window.innerWidth = 1280;
    const user = userEvent.setup();
    const { container } = renderAt("/doctor");
    const bar = (await screen.findByRole("contentinfo")) as HTMLElement;
    const button = await within(bar).findByRole("button", { name: "Thing ok, OK" });
    expect(screen.queryByRole("region", { name: "Thing ok" })).not.toBeInTheDocument();
    fireEvent.mouseEnter(button.parentElement as HTMLElement);
    const pane = await screen.findByRole("region", { name: "Thing ok" });
    expect(within(pane).getByText("Jobs: 3")).toBeInTheDocument();
    expect(within(pane).getByRole("button", { name: "Logs" })).toBeInTheDocument();
    expect(pane.querySelector("section > p svg")).toBeNull(); // the trigger color and accessible state already convey status
    await expectAccessible(container);
    fireEvent.mouseLeave(button.parentElement as HTMLElement);
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(screen.queryByRole("region", { name: "Thing ok" })).not.toBeInTheDocument();
    button.focus(); // keyboard: tabbing onto the button is the same focus
    expect(await screen.findByRole("region", { name: "Thing ok" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("region", { name: "Thing ok" })).not.toBeInTheDocument();
    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    await user.unhover(button);
    await user.click(document.body);
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(screen.queryByRole("region", { name: "Thing ok" })).not.toBeInTheDocument();
  });

  it("opens the calendar panel and explains an unavailable connection", async () => {
    mockApi({
      ...states,
      "/api/org/calendar": {
        since: "2026-11-02T00:00:00Z",
        until: "2026-11-09T00:00:00Z",
        events: [],
        problem: "No calendar connected.",
        fix: "hz setup",
      },
      "/api/org/calendar-reminders-status": { enabled: true, service: "not installed" },
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    const bar = await screen.findByRole("contentinfo");
    const calendar = await within(bar).findByRole("button", { name: /^Calendar: .*, error$/ });
    expect(calendar.closest("[data-state]")).toHaveAttribute("data-state", "bad");
    expect(calendar.querySelector("[data-glyph=unknown]")).not.toBeNull();
    await user.click(calendar);
    const summary = await screen.findByRole("region", { name: "Calendar summary" });
    await user.click(within(summary).getByRole("button", { name: "Open calendar" }));
    const panel = await screen.findByRole("region", { name: "Calendar" });
    expect(await within(panel).findByText("No calendar connected.")).toBeInTheDocument();
    expect(await within(panel).findByText(/Calendar reminders need a background worker/)).toBeInTheDocument();
    await user.click(within(panel).getByRole("link", { name: "Connect a calendar…" }));
    expect(await screen.findByRole("dialog", { name: "Settings" })).toBeInTheDocument();
  });

  it("shows current and next meeting together and opens the week", async () => {
    const now = Date.now();
    const current = {
      title: "Standup",
      start: new Date(now - 60_000).toISOString(),
      end: new Date(now + 300_000).toISOString(),
    };
    const next = {
      title: "Review",
      start: new Date(now + 600_000).toISOString(),
      end: new Date(now + 900_000).toISOString(),
    };
    mockApi({
      ...states,
      "/api/org/agenda": { current, next, problem: null },
      "/api/org/calendar": {
        since: new Date(now).toISOString(),
        until: new Date(now + 604800000).toISOString(),
        events: [{ ...next, id: "2", all_day: false }],
        problem: null,
        fix: null,
      },
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    const footer = await screen.findByRole("contentinfo");
    const calendar = await within(footer).findByRole("button", { name: /^Calendar: In Standup, \d+ min left.*, off$/ });
    expect(calendar).not.toHaveTextContent(/Standup|Review/);
    expect(calendar.querySelector('[data-glyph="meeting"] [data-label]')).toHaveTextContent(/^[1-9]$/);
    await user.hover(calendar);
    const summary = await screen.findByRole("region", { name: "Calendar summary" });
    expect(within(summary).getByText("In Standup")).toBeInTheDocument();
    expect(within(summary).getAllByText(/\d+ min left/)[0]).toBeInTheDocument();
    expect(within(summary).getByText(/Next: Review/)).toBeInTheDocument();
    for (const name of [/^Decline/, /^Extend/, /^Tell the next meeting/])
      expect(within(summary).getByRole("button", { name })).toBeDisabled();
    expect(within(summary).getByRole("link", { name: "Allow your Heinzels to change your calendar" })).toHaveAttribute(
      "href",
      expect.stringContaining("settings=connectors"),
    );
    await user.click(within(summary).getByRole("button", { name: "Open calendar" }));
    const panel = await screen.findByRole("region", { name: "Calendar" });
    expect(await within(panel).findByText("Review")).toBeInTheDocument();
  });

  it("only renders safe conference links in the calendar", async () => {
    const now = Date.now();
    const base = {
      start: new Date(now - 60_000).toISOString(),
      end: new Date(now + 300_000).toISOString(),
      all_day: false,
    };
    mockApi({
      ...states,
      "/api/org/calendar": {
        since: new Date(now).toISOString(),
        until: new Date(now + 604800000).toISOString(),
        problem: null,
        fix: null,
        events: [
          { ...base, id: "1", title: "Safe", conference_url: "https://meet.example/test" },
          { ...base, id: "2", title: "HTTP", conference_url: "http://meet.example/test" },
          { ...base, id: "3", title: "Missing" },
          { ...base, id: "4", title: "Script", conference_url: "javascript:alert(1)" },
          { ...base, id: "5", title: "Credentials", conference_url: "https://user:pass@meet.example/test" },
          { ...base, id: "6", title: "Backslash", conference_url: "https://meet.example\\evil.test" },
        ],
      },
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.click(
      await within(await screen.findByRole("contentinfo")).findByRole("button", {
        name: /^Calendar: .*, error$/,
      }),
    );
    const summary = await screen.findByRole("region", { name: "Calendar summary" });
    await user.click(within(summary).getByRole("button", { name: "Open calendar" }));
    const panel = await screen.findByRole("region", { name: "Calendar" });
    const links = await within(panel).findAllByRole("link", { name: "Join meeting" });
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAttribute("href", "https://meet.example/test");
    expect(links[1]).toHaveAttribute("href", "http://meet.example/test");
    for (const link of links) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noreferrer noopener");
    }
  });

  it("shows the installed changelog and a compact GitHub release link", async () => {
    mockApi(states);
    window.innerWidth = 1280;
    const user = userEvent.setup();
    renderAt("/doctor");
    const bar = (await screen.findByRole("contentinfo")) as HTMLElement;
    await user.click(await within(bar).findByRole("button", { name: "v0.4.0" }));
    const pane = await screen.findByRole("region", { name: "v0.4.0" });
    const about = within(pane).getByRole("region", { name: "About Heinzelmännchen" });
    expect(within(about).getByRole("img", { name: "Heinzelmännchen" })).toBeInTheDocument();
    const tagline = within(about).getByText("Heinzels accelerating product teams,");
    expect(tagline.parentElement).toHaveClass("text-[10px]");
    expect(within(about).getByText("like the little elves from Cologne")).toBeInTheDocument();
    expect(within(about).queryByText("built with AI in Berlin")).not.toBeInTheDocument();
    expect(await within(pane).findByText("Something new")).toBeInTheDocument();
    expect(within(pane).queryByText("What changed in this release")).not.toBeInTheDocument();
    expect(within(pane).queryByRole("link", { name: "Full changelog" })).not.toBeInTheDocument();
    expect(within(pane).getByRole("link", { name: "GitHub release" })).toHaveAttribute(
      "href",
      "https://github.com/helmut-hoffer-von-ankershoffen/heinzel/releases/tag/v0.4.0",
    );
  });
});
