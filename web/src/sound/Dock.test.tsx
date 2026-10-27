import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SchemaSoundList, SchemaSoundscapeView } from "@/api/client";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { clampToWindow, nearSlot, placePopover, resetDock } from "./dock";
import { resetPageFrame } from "./queries";
import { resetSound } from "./store";

const ORIGIN = "https://www.youtube-nocookie.com";
const base = { module: null, plugin: null, selected: false, effective: false, fade_seconds: 4 };
const off: SchemaSoundscapeView = { ...base, name: "off", title: "Off", kind: "off" };
const fm: SchemaSoundscapeView = {
  ...base,
  name: "claude-fm",
  title: "Claude FM",
  kind: "stream",
  stream: {
    provider: "youtube",
    video: "tRsQsTMvPNg",
    embed_url: `${ORIGIN}/embed/tRsQsTMvPNg`,
    frame_origin: ORIGIN,
    watch_url: "https://www.youtube.com/watch?v=tRsQsTMvPNg",
  },
};
const focus: SchemaSoundscapeView = {
  ...base,
  name: "focus",
  title: "Die Heinzelmännchen",
  kind: "layers",
  layers: [],
};

function list(selected: string): SchemaSoundList {
  return {
    selected,
    effective: selected,
    soundscapes: [off, fm, focus].map((s) => ({ ...s, selected: s.name === selected, effective: s.name === selected })),
    signals: {},
    volume: 50,
    follow_theme: false,
    mute_in_meetings: true,
    duck_while_typing: true,
  };
}

const api = (selected = "claude-fm") =>
  mockApi({ ...shell, "/api/system/sound/list": list(selected), "/api/system/sound/use": list("off") });

const player = () => screen.findByRole("region", { name: "Claude FM player" });
const dockSlot = () => document.querySelector("[data-sound-dock]") as HTMLElement;

async function play() {
  const user = userEvent.setup();
  renderAt("/");
  await user.click(await screen.findByRole("button", { name: /^Play Claude FM$/ }));
  return { user, card: await player() };
}

beforeEach(() => resetDock());
afterEach(() => {
  resetSound();
  resetPageFrame();
  resetDock();
  vi.restoreAllMocks();
});

describe("the mini-player docked in the sidebar", () => {
  it("is laid over the dock slot between the clock line and the account row, as wide as the column", async () => {
    api();
    const { card } = await play();
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0] as HTMLElement;
    expect(card.parentElement).toBe(document.body); // never moved in the DOM, so its iframe never reloads
    expect(card).toHaveAttribute("data-dock", "docked");
    expect(card.style.width).toBe("0px"); // the slot's width (jsdom has no layout)
    expect(dockSlot().className).toContain("-mx-2"); // the full width of the column, past its padding
    const precedes = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    expect(precedes(nav, dockSlot())).toBe(true);
    const account = dockSlot().nextElementSibling as HTMLElement;
    expect(within(account).getAllByRole("button", { name: /^Pause Claude FM$/ })).toHaveLength(1);
    expect(dockSlot().parentElement).toBe(account.parentElement);
  });

  it("pops out with its button, docks again with its button, and remembers both", async () => {
    api();
    const { user, card } = await play();
    await user.click(within(card).getByRole("button", { name: "Pop the player out of the sidebar" }));
    const floating = await player();
    expect(floating).toHaveAttribute("data-dock", "floating");
    expect(floating.className).toContain("fixed");
    expect(floating.parentElement).toBe(document.body);
    expect(JSON.parse(localStorage.getItem("hz.sound.player") ?? "{}").docked).toBe(false);
    await user.click(within(floating).getByRole("button", { name: "Dock the player in the sidebar" }));
    expect(await player()).toHaveAttribute("data-dock", "docked");
    expect(JSON.parse(localStorage.getItem("hz.sound.player") ?? "{}").docked).toBe(true);
  });

  it("follows the dock's measured bounds and releases its reserved height when floating", async () => {
    api();
    let bounds = { left: 12, top: 400, width: 240 };
    const originalBounds = HTMLElement.prototype.getBoundingClientRect;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      if (!this.hasAttribute("data-sound-dock")) return originalBounds.call(this);
      return new DOMRect(bounds.left, bounds.top, bounds.width, 200);
    });
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (this: HTMLElement) {
      return this.hasAttribute("data-dock") ? 200 : 0;
    });
    const { user, card } = await play();
    expect(card).toHaveStyle({ left: "12px", top: "400px", width: "240px" });
    expect(dockSlot()).toHaveStyle({ height: "200px" });
    bounds = { left: 20, top: 360, width: 260 };
    fireEvent(window, new Event("resize"));
    await waitFor(() => expect(card).toHaveStyle({ left: "20px", top: "360px", width: "260px" }));
    await user.click(within(card).getByRole("button", { name: "Pop the player out of the sidebar" }));
    expect(card).toHaveAttribute("data-dock", "floating");
    expect(dockSlot().style.height).toBe("");
  });

  it("keeps playing: the very same frame stays mounted when it docks and undocks", async () => {
    api();
    const { user, card } = await play();
    const frame = within(card).getByTitle("Claude FM player") as HTMLIFrameElement;
    const mounted = vi.fn();
    const watch = new MutationObserver((records) => {
      for (const record of records) for (const node of record.removedNodes) if (node.contains(frame)) mounted();
    });
    watch.observe(document.body, { childList: true, subtree: true });
    await user.click(within(card).getByRole("button", { name: "Pop the player out of the sidebar" }));
    expect(await screen.findByRole("region", { name: "Claude FM player" })).toHaveAttribute("data-dock", "floating");
    await user.click(screen.getByRole("button", { name: "Dock the player in the sidebar" }));
    await waitFor(() => expect(card).toHaveAttribute("data-dock", "docked"));
    await user.click(screen.getByRole("button", { name: "Collapse navigation" }));
    await waitFor(() => expect(card).toHaveAttribute("data-dock", "floating"));
    await user.click(screen.getByRole("button", { name: "Expand navigation" }));
    await waitFor(() => expect(card).toHaveAttribute("data-dock", "docked"));
    watch.disconnect();
    expect(mounted).not.toHaveBeenCalled(); // never removed from the page, not even for a moment
    expect(frame.isConnected).toBe(true);
    expect(within(card).getByTitle("Claude FM player")).toBe(frame);
    expect(document.querySelectorAll("iframe")).toHaveLength(1);
  });

  it("detaches when dragged out and snaps back when dropped near the dock spot", async () => {
    api();
    const { card } = await play();
    const header = card.querySelector("header") as HTMLElement;
    vi.spyOn(dockSlot(), "getBoundingClientRect").mockReturnValue(new DOMRect(0, 500, 256, 0));
    vi.spyOn(card, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 500, 256, 190));
    fireEvent.pointerDown(header, { clientX: 20, clientY: 510, pointerId: 1 });
    fireEvent.pointerMove(header, { clientX: 420, clientY: 310, pointerId: 1 });
    fireEvent.pointerUp(header, { clientX: 420, clientY: 310, pointerId: 1 });
    const floating = await screen.findByRole("region", { name: "Claude FM player" });
    await waitFor(() => expect(floating).toHaveAttribute("data-dock", "floating"));
    expect(floating.style.left).toBe("400px");
    expect(floating.style.top).toBe("300px");
    // Dragged back to within reach of the dock spot.
    vi.spyOn(floating, "getBoundingClientRect").mockReturnValue(new DOMRect(400, 300, 288, 190));
    const handle = floating.querySelector("header") as HTMLElement;
    fireEvent.pointerDown(handle, { clientX: 420, clientY: 310, pointerId: 1 });
    fireEvent.pointerMove(handle, { clientX: 50, clientY: 520, pointerId: 1 });
    fireEvent.pointerUp(handle, { clientX: 50, clientY: 520, pointerId: 1 });
    await waitFor(() => expect(document.querySelector("[data-dock='docked']")).not.toBeNull());
  });

  it("moves with the arrow keys when floating", async () => {
    api();
    const { user, card } = await play();
    await user.click(within(card).getByRole("button", { name: "Pop the player out of the sidebar" }));
    const floating = await player();
    const move = within(floating).getByRole("button", { name: "Move the player (arrow keys)" });
    move.focus();
    const left = Number.parseInt(floating.style.left, 10);
    await user.keyboard("{ArrowRight}");
    expect(Number.parseInt((await player()).style.left, 10)).toBe(left + 16);
  });

  it("floats when the sidebar collapses and docks again when it expands", async () => {
    api();
    const { user, card } = await play();
    expect(card).toHaveAttribute("data-dock", "docked");
    await user.click(screen.getByRole("button", { name: "Collapse navigation" }));
    expect(await screen.findByRole("region", { name: "Claude FM player" })).toHaveAttribute("data-dock", "floating");
    expect(within(await player()).queryByRole("button", { name: "Dock the player in the sidebar" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Expand navigation" }));
    await waitFor(() => expect(document.querySelector("[data-dock='docked']")).not.toBeNull());
  });

  it("stays floating after a collapse and an expand when it was popped out before", async () => {
    api();
    const { user, card } = await play();
    await user.click(within(card).getByRole("button", { name: "Pop the player out of the sidebar" }));
    await user.click(screen.getByRole("button", { name: "Collapse navigation" }));
    await user.click(screen.getByRole("button", { name: "Expand navigation" }));
    expect(await player()).toHaveAttribute("data-dock", "floating");
  });

  it("works without storage", async () => {
    const set = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key: string, value: string) {
      if (key === "hz.sound.player") throw new Error("denied");
      return set.call(this, key, value);
    });
    api();
    const { user, card } = await play();
    await user.click(within(card).getByRole("button", { name: "Pop the player out of the sidebar" }));
    expect(await player()).toHaveAttribute("data-dock", "floating");
  });

  it("has no accessibility violations docked or floating", async () => {
    api();
    const { user, card } = await play();
    // axe can't look into a cross-origin frame in jsdom: the card's header is the part that is ours.
    await expectAccessible(card.querySelector("header") as HTMLElement);
    await user.click(within(card).getByRole("button", { name: "Pop the player out of the sidebar" }));
    await expectAccessible((await player()).querySelector("header") as HTMLElement);
  });
});

describe("the sound popover", () => {
  it("is in the body, above the player, with the controls and a way to Settings", async () => {
    const calls = api().calls;
    const { user } = await play();
    const speaker = (await screen.findAllByRole("button")).find(
      (button) => button.getAttribute("aria-haspopup") === "dialog",
    );
    expect(speaker).toBeTruthy();
    await user.pointer({ keys: "[MouseRight]", target: speaker });
    const dialog = await screen.findByRole("dialog", { name: "Sound" });
    expect(dialog.parentElement).toBe(document.body);
    expect(dialog.className).toContain("z-(--layer-popover)");
    expect(dialog.className).toContain("fixed");
    expect((await player()).className).toContain("z-(--layer-media)");
    for (const name of ["Softer while I type", "Follow my theme", "Mute during meetings"])
      expect(within(dialog).getByRole("switch", { name })).toBeInTheDocument();
    expect(within(dialog).queryByRole("switch", { name: "Sound on" })).not.toBeInTheDocument();
    expect(
      within(dialog)
        .getAllByRole("radio")
        .map((r) => r.textContent),
    ).toEqual(["Claude FMPlays from YouTube", "Die Heinzelmännchen"]);
    expect(within(dialog).queryByRole("radio", { name: /^Off/ })).not.toBeInTheDocument();
    const settingsShortcut = within(dialog).getByRole("link", { name: "More in Settings" });
    expect(settingsShortcut).toHaveAttribute("href", "/?settings=hz");
    expect(settingsShortcut.querySelector("svg")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("switch", { name: "Softer while I type" }));
    await waitFor(() =>
      expect(
        calls.some((c) => c.url.includes("/system/config/set") && c.url.includes("key=system.sound.duck_while_typing")),
      ).toBe(true),
    );
    await expectAccessible(dialog);
  });

  it("choosing a real soundscape selects it when the configured choice is off", async () => {
    const calls = api("off").calls;
    const user = userEvent.setup();
    renderAt("/");
    await user.click(await screen.findByRole("button", { name: "Choose a soundscape" }));
    const dialog = await screen.findByRole("dialog", { name: "Sound" });
    await user.click(within(dialog).getByRole("radio", { name: /^Claude FM/ }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/system/sound/use?name=claude-fm"))).toBe(true));
  });
});

describe("placement", () => {
  const view = { width: 1000, height: 700 };
  const size = { width: 288, height: 400 };
  it("keeps the popover inside the window, shifted in from the left edge", () => {
    const at = placePopover({ left: 10, right: 40, top: 650, bottom: 690 }, size, "above", view);
    expect(at.left).toBeGreaterThanOrEqual(8);
    expect(at.top).toBeGreaterThanOrEqual(8);
    expect(at.left + size.width).toBeLessThanOrEqual(view.width - 8);
  });
  it("goes below the anchor when there is no room above, and beside the rail", () => {
    expect(placePopover({ left: 600, right: 640, top: 100, bottom: 140 }, size, "above", view).top).toBe(146);
    const beside = placePopover({ left: 0, right: 56, top: 600, bottom: 640 }, size, "right", view);
    expect(beside.left).toBe(64);
    expect(beside.top + size.height).toBeLessThanOrEqual(view.height - 8);
  });
  it("flips to the left of an anchor at the window's right edge", () => {
    expect(placePopover({ left: 900, right: 960, top: 600, bottom: 640 }, size, "right", view).left).toBe(604);
  });
  it("clamps a floating card to the window and recognises the dock spot", () => {
    expect(clampToWindow(-50, 900, { width: 288, height: 190 }, view)).toEqual({ x: 0, y: 510 });
    expect(nearSlot(30, 530, { left: 0, top: 500 })).toBe(true);
    expect(nearSlot(300, 100, { left: 0, top: 500 })).toBe(false);
  });
});
