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

describe("The status bar's clock", () => {
  it("does not render a clock when the clock plugin is absent", async () => {
    mockApi({
      ...shell,
      "/api/system/info": { version: "0.4.0", modules: [] },
      "/api/system/ui/status": [
        {
          id: "clock:primary-zone",
          module: "clock",
          plugin: "heinzel-clock",
          order: 900,
          refresh: 60,
          problem: null,
          slots: { footer: true },
          item: { text: "Berlin", tone: "neutral", icon: "clock" },
        },
      ],
    });
    renderAt("/doctor");
    const bar = await screen.findByRole("contentinfo");
    expect(within(bar).queryByRole("button", { description: /World Clock/ })).not.toBeInTheDocument();
    expect(bar.textContent).not.toMatch(/\b\d{2}:\d{2}:\d{2}\b/);
  });

  it("keeps the zones open while the pointer moves up to them, and their link opens the clock's settings", async () => {
    mockApi(answers);
    const user = userEvent.setup();
    const { container } = renderAt("/doctor");
    const clock = await screen.findByRole("button", { description: "World Clock: time for the team and stakeholders" });
    const wrapper = clock.parentElement as HTMLElement;
    fireEvent.mouseEnter(wrapper);
    const zones = await screen.findByRole("region", { name: "World Clock: time for the team and stakeholders" });
    expect(await within(zones).findByText("New York")).toBeInTheDocument();
    // The first zone is yours: the clock shows its time, and the panel lists it first, marked, then the others, all on
    // the 24-hour clock.
    const rows = within(zones).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[0] as HTMLElement).getByText("Berlin")).toBeInTheDocument();
    expect(within(rows[0] as HTMLElement).getByText("Primary")).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByText("New York")).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).queryByText("Primary")).not.toBeInTheDocument();
    for (const row of rows) expect(row.textContent).toMatch(/\d{2}:\d{2}$/);
    const time = clock.querySelector("time") as HTMLTimeElement;
    expect(time).toHaveTextContent(/^\d{2}:\d{2}:\d{2}$/);
    expect(time.textContent).not.toMatch(/[ap]m/i);
    // Leaving the clock for the panel above: the panel is inside the hovered element, so nothing closes.
    fireEvent.mouseLeave(wrapper);
    fireEvent.mouseEnter(wrapper);
    await new Promise((resolve) => setTimeout(resolve, 250));
    await user.click(within(zones).getByRole("link", { name: "Edit time zones and working hours…" }));
    expect(await screen.findByRole("dialog", { name: "Settings" })).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("closes a short while after the pointer leaves, opens pinned on click, and closes on Escape", async () => {
    mockApi(answers);
    const user = userEvent.setup();
    renderAt("/doctor");
    const clock = await screen.findByRole("button", { description: "World Clock: time for the team and stakeholders" });
    const wrapper = clock.parentElement as HTMLElement;
    fireEvent.mouseEnter(wrapper);
    fireEvent.mouseLeave(wrapper);
    expect(screen.getByRole("region", { name: "World Clock: time for the team and stakeholders" })).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(
      screen.queryByRole("region", { name: "World Clock: time for the team and stakeholders" }),
    ).not.toBeInTheDocument();
    await user.click(clock);
    expect(clock).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{Escape}");
    expect(clock).toHaveAttribute("aria-expanded", "false");
  });
});

describe("Mounted footer measurements", () => {
  afterEach(() => vi.restoreAllMocks());
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
    expect(within(footer).getByRole("button", { name: /CPU Memory Load/ })).toBeInTheDocument();
    expect(within(footer).getByRole("button", { name: /System health/ })).toBeInTheDocument();
    expect(within(footer).getByRole("button", { name: "0 Heinzelmännchen, off" })).toBeInTheDocument();
    expect(within(footer).queryByRole("button", { name: "More status items" })).not.toBeInTheDocument();
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

  it("gives each state its own colour and shape, named for assistive tech, and passes axe", async () => {
    mockApi(states);
    window.innerWidth = 1280;
    const { container } = renderAt("/doctor");
    const bar = (await screen.findByRole("contentinfo")) as HTMLElement;
    expect(within(bar).queryByText("helpers accelerating product teams")).not.toBeInTheDocument();
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
      expect(button.querySelectorAll("svg")).toHaveLength(2); // the icon and the shape of the state
    }
    await expectAccessible(container);
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

  it("shows the calendar as a red icon that says so and links to connect one", async () => {
    mockApi(states);
    window.innerWidth = 1280;
    const user = userEvent.setup();
    renderAt("/doctor");
    const bar = (await screen.findByRole("contentinfo")) as HTMLElement;
    const calendar = await within(bar).findByRole("button", { name: "No calendar connected, error" });
    expect(calendar).toHaveClass("text-bad");
    await user.click(calendar);
    const pane = await screen.findByRole("region", { name: "No calendar connected" });
    await user.click(within(pane).getByRole("link", { name: "Connect a calendar…" }));
    expect(await screen.findByRole("dialog", { name: "Settings" })).toBeInTheDocument();
  });

  it("shows what changed in the running release from the installed changelog, with the release on GitHub", async () => {
    mockApi(states);
    window.innerWidth = 1280;
    const user = userEvent.setup();
    renderAt("/doctor");
    const bar = (await screen.findByRole("contentinfo")) as HTMLElement;
    await user.click(await within(bar).findByRole("button", { name: "v0.4.0" }));
    const pane = await screen.findByRole("region", { name: "v0.4.0" });
    const about = within(pane).getByRole("region", { name: "About Heinzelmännchen" });
    expect(within(about).getByRole("img", { name: "Heinzelmännchen" })).toBeInTheDocument();
    const tagline = within(about).getByText("helpers accelerating product teams,");
    const origin = within(about).getByText("like the little elves from Cologne");
    expect(tagline.nextElementSibling).toBe(origin);
    expect(tagline).toHaveClass("block");
    expect(origin).toHaveClass("block");
    expect(within(about).queryByText("built with AI in Berlin")).not.toBeInTheDocument();
    expect(await within(pane).findByText("Something new")).toBeInTheDocument();
    expect(within(pane).getByRole("link", { name: "Release notes on GitHub" })).toHaveAttribute(
      "href",
      "https://github.com/helmut-hoffer-von-ankershoffen/heinzel/releases/tag/v0.4.0",
    );
  });
});
