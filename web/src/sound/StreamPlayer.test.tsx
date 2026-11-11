import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SchemaSoundscapeView } from "@/api/client";
import { expectPaneStructure } from "@/test/pane";
import { StreamPlayer } from "./StreamPlayer";
import { resetSound, soundState, toggle } from "./store";
import { rememberYouTubeAccount } from "./stream";

const ORIGIN = "https://www.youtube-nocookie.com";
const stream = {
  provider: "youtube",
  video: "tRsQsTMvPNg",
  embed_url: `${ORIGIN}/embed/tRsQsTMvPNg`,
  frame_origin: ORIGIN,
  watch_url: "https://www.youtube.com/watch?v=tRsQsTMvPNg",
};
const soundscape: SchemaSoundscapeView = {
  name: "fm",
  title: "FM",
  kind: "stream",
  module: "system.sound",
  plugin: "heinzel",
  selected: true,
  effective: true,
  fade_seconds: 0,
  stream,
};
const level = { gain: 0.5, seconds: 0, playing: true };
const player = () => screen.getByTitle<HTMLIFrameElement>("FM player");
const info = () => screen.getByRole("button", { name: /Playback or sign-in trouble\?/ });
const attention = () => document.querySelector("[data-attention]");
const openHelp = () => fireEvent.click(info());
function message(event: string, info?: unknown, frame = player(), origin = new URL(frame.src).origin) {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data: { event, info }, source: frame.contentWindow, origin }));
  });
}
function mount() {
  return render(<StreamPlayer soundscape={soundscape} stream={stream} level={level} />);
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
  resetSound();
});

describe("stream recovery", () => {
  it.each([false, true])("explains same-profile recovery for browser or standalone mode: %s", (standalone) => {
    const matchMedia = window.matchMedia;
    vi.spyOn(window, "matchMedia").mockImplementation((query) => ({
      ...matchMedia(query),
      matches: query === "(display-mode: standalone)" && standalone,
    }));
    mount();
    openHelp();
    expect(screen.getByText(/same browser profile where you signed in to YouTube/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use YouTube sign-in" }));
    openHelp();
    expect(screen.getByText(/Embedded sign-in may still be restricted/)).toBeInTheDocument();
    expect(screen.queryByText(/Heinzel desktop has its own browser session/)).toBeNull();
  });

  it("offers desktop copy recovery without offering the account player", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("heinzelDesktop", { lifecycle: vi.fn() });
    vi.stubGlobal("navigator", {
      userAgent: "fixture",
      platform: "fixture",
      maxTouchPoints: 0,
      clipboard: { writeText },
    });
    rememberYouTubeAccount(true);
    mount();
    expect(new URL(player().src).origin).toBe(ORIGIN);
    openHelp();
    expect(screen.getByText(/Heinzel desktop has its own browser session/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use YouTube sign-in" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Play on YouTube" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Copy YouTube link" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Link copied"));
    expect(writeText).toHaveBeenCalledExactlyOnceWith("https://www.youtube.com/watch?v=tRsQsTMvPNg");
  });

  it("reaches desktop copy recovery by keyboard", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("heinzelDesktop", { lifecycle: vi.fn() });
    mount();
    info().focus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Copy YouTube link" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("status")).toHaveTextContent("Link copied");
  });

  it.each(["denied", "unavailable"])("offers a selectable URL if clipboard is %s", async (mode) => {
    vi.stubGlobal("heinzelDesktop", { lifecycle: vi.fn() });
    const clipboard = mode === "denied" ? { writeText: vi.fn().mockRejectedValue(new Error("denied")) } : undefined;
    vi.stubGlobal("navigator", { userAgent: "fixture", platform: "fixture", maxTouchPoints: 0, clipboard });
    mount();
    openHelp();
    fireEvent.click(screen.getByRole("button", { name: "Copy YouTube link" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Couldn't copy"));
    expect(screen.getByRole("textbox", { name: "YouTube video link" })).toHaveValue(
      "https://www.youtube.com/watch?v=tRsQsTMvPNg",
    );
  });

  it("allows keyboard selection of the fallback when copying is denied", async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("denied"));
    vi.stubGlobal("heinzelDesktop", { lifecycle: vi.fn() });
    mount();
    info().focus();
    await user.tab();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("status")).toHaveTextContent("Couldn't copy");
    await user.keyboard("{Shift>}{Tab}{/Shift}");
    const field = screen.getByRole<HTMLInputElement>("textbox", { name: "YouTube video link" });
    expect(field).toHaveFocus();
    expect(field).toHaveAttribute("readonly");
    expect(field.selectionStart).toBe(0);
    expect(field.selectionEnd).toBe(field.value.length);
  });

  it("uses mute and pause on iPhone when the stream cannot change its volume", () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (iPhone) AppleWebKit/605.1.15");
    const view = mount();
    const post = vi.spyOn(player().contentWindow as Window, "postMessage");
    const sent = () => post.mock.calls.map(([data]) => JSON.parse(String(data)).func);
    message("onReady");
    expect(sent()).toEqual(["mute", "unMute", "playVideo"]);
    post.mockClear();
    message("initialDelivery");
    message("onReady");
    expect(sent()).toEqual(["unMute", "unMute"]);
    post.mockClear();
    view.rerender(<StreamPlayer soundscape={soundscape} stream={stream} level={{ ...level, gain: 0 }} />);
    expect(sent()).toEqual(["mute", "playVideo"]);
    post.mockClear();
    message("initialDelivery");
    expect(sent()).toEqual(["mute"]);
    post.mockClear();
    view.rerender(<StreamPlayer soundscape={soundscape} stream={stream} level={level} />);
    expect(sent()).toEqual(["unMute", "playVideo"]);
    post.mockClear();
    view.rerender(
      <StreamPlayer soundscape={soundscape} stream={stream} level={{ gain: 0, seconds: 4, playing: false }} />,
    );
    expect(sent()).toEqual(["mute", "pauseVideo"]);
  });

  it("switches to the account player only by choice, remembers it, and lets the viewer reverse it", () => {
    const { unmount } = mount();
    expect(new URL(player().src).origin).toBe(ORIGIN);
    openHelp();
    fireEvent.click(screen.getByRole("button", { name: "Use YouTube sign-in" }));
    expect(new URL(player().src).origin).toBe("https://www.youtube.com");
    expect(player().getAttribute("sandbox")).toContain("allow-storage-access-by-user-activation");
    unmount();
    mount();
    expect(new URL(player().src).origin).toBe("https://www.youtube.com");
    openHelp();
    fireEvent.click(screen.getByRole("button", { name: "Use private player" }));
    expect(new URL(player().src).origin).toBe(ORIGIN);
    expect(player().getAttribute("sandbox")).not.toContain("allow-storage-access-by-user-activation");
  });

  it("flags recovery if the player is ready but never plays, and retry creates a fresh private frame", () => {
    vi.useFakeTimers();
    mount();
    const first = player();
    message("onReady");
    expect(attention()).toBeNull();
    act(() => vi.advanceTimersByTime(15_000));
    expect(attention()).not.toBeNull();
    openHelp();
    fireEvent.click(screen.getByRole("button", { name: "Retry player" }));
    expect(player()).not.toBe(first);
    expect(new URL(player().src).origin).toBe(ORIGIN);
    expect(attention()).toBeNull();
    // A stale frame cannot confirm that the retry is playing.
    message("onStateChange", 1, first);
    act(() => vi.advanceTimersByTime(15_000));
    expect(attention()).not.toBeNull();
  });

  it.each([
    ["onStateChange", 1],
    ["infoDelivery", { playerState: 1 }],
  ])("cancels the recovery timeout only after %s confirms playback", (event, info) => {
    vi.useFakeTimers();
    mount();
    message(event, info);
    act(() => vi.advanceTimersByTime(15_000));
    expect(attention()).toBeNull();
  });

  it("does not suggest sign-in when the viewer pauses after playback started", () => {
    vi.useFakeTimers();
    mount();
    message("onStateChange", 1);
    message("onStateChange", 2);
    act(() => vi.advanceTimersByTime(15_000));
    expect(attention()).toBeNull();
  });

  it.each(["onError", "onAutoplayBlocked"])("flags recovery immediately after %s", (event) => {
    mount();
    message(event, 150);
    expect(attention()).not.toBeNull();
  });

  it("ignores events from the old private origin after switching, and sends commands to the account origin", () => {
    rememberYouTubeAccount(true);
    mount();
    const post = vi.spyOn(player().contentWindow as Window, "postMessage");
    message("onError", 150, player(), ORIGIN);
    expect(attention()).toBeNull();
    message("onReady");
    expect(post).toHaveBeenCalled();
    expect(post.mock.calls.every((call) => (call as unknown[])[1] === "https://www.youtube.com")).toBe(true);
  });

  it("keeps the account choice for this player even when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    mount();
    openHelp();
    fireEvent.click(screen.getByRole("button", { name: "Use YouTube sign-in" }));
    expect(new URL(player().src).origin).toBe("https://www.youtube.com");
  });

  it.each(["Play on YouTube", "Open on YouTube"])("%s opens the actual video and stops the embedded player", (name) => {
    toggle();
    mount();
    openHelp();
    const link = screen.getByRole("link", { name });
    expect(link).toHaveAttribute("href", stream.watch_url);
    expect(link).toHaveAttribute("target", "_blank");
    fireEvent.click(link);
    expect(soundState()).toMatchObject({ playing: false, started: false });
  });
});

describe("video link placement", () => {
  it("retains the header link in a browser and no bottom row", () => {
    mount();
    const link = screen.getByRole("link", { name: "Open on YouTube" });
    expect(link.closest("header")).not.toBeNull();
    expect(link).toHaveAttribute("title", "Open on YouTube");
    expect(link.textContent?.trim()).toBe("");
    expect(player().closest("section")?.lastElementChild).toBe(player().parentElement);
  });

  it("does not expose a nonfunctional permanent desktop header link", () => {
    vi.stubGlobal("heinzelDesktop", { lifecycle: vi.fn() });
    mount();
    expect(screen.queryByRole("link", { name: "Open on YouTube" })).toBeNull();
  });
});

describe("playback help pane", () => {
  it("takes no line of the card: the help is only an icon button on the video until it is opened", () => {
    mount();
    expect(info()).toHaveAttribute("aria-expanded", "false");
    expect(info()).toHaveAttribute("title", "Playback or sign-in trouble?");
    // It sits over the video area (the hover/focus group), not in the header, and is revealed by hover or focus.
    const video = player().parentElement as HTMLElement;
    expect(video).toHaveClass("group/video");
    expect(video.contains(info())).toBe(true);
    expect(info().closest("header")).toBeNull();
    expect(info()).toHaveClass("opacity-0", "group-hover/video:opacity-100", "group-focus-within/video:opacity-100");
    expect(info()).toHaveClass("focus-visible:opacity-100", "pointer-coarse:opacity-70");
    expect(screen.queryByText("YouTube asking you to sign in?")).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry player" })).toBeNull();
  });

  it("opens on hover, focus and tap, and closes on Escape, keeping the action row under a divider", () => {
    mount();
    fireEvent.mouseEnter(info());
    const pane = screen.getByRole("region", { name: "Playback or sign-in trouble?" });
    expect(pane).toHaveTextContent("same browser profile where you signed in to YouTube");
    expect(screen.getByRole("link", { name: "Play on YouTube" }).parentElement).toHaveClass("border-t");
    // The shared action row: the first action leads with its words, the rest are named icons.
    expectPaneStructure(pane);
    fireEvent.keyDown(info(), { key: "Escape" });
    expect(screen.queryByRole("region", { name: "Playback or sign-in trouble?" })).toBeNull();
    fireEvent.focus(info());
    expect(info()).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(info(), { key: "Escape" });
    expect(info()).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(info());
    expect(info()).toHaveAttribute("aria-expanded", "true");
    fireEvent.pointerDown(document.body);
    expect(info()).toHaveAttribute("aria-expanded", "false");
  });

  it("shows an attention dot, named for assistive technology, only when playback needs attention", () => {
    mount();
    expect(attention()).toBeNull();
    message("onError", 150);
    expect(attention()).not.toBeNull();
    expect(info()).toHaveAccessibleName("Playback or sign-in trouble? (needs attention)");
    expect(info()).toHaveClass("opacity-100");
    expect(info()).not.toHaveClass("opacity-0");
    expect(info()).toHaveAttribute("aria-expanded", "false");
  });

  it.each([
    ["onError", () => message("onError", 150)],
    ["onAutoplayBlocked", () => message("onAutoplayBlocked")],
    ["a 15 s stall", () => act(() => vi.advanceTimersByTime(15_000))],
  ])("%s marks the button and one click reaches recovery", (_name, trigger) => {
    vi.useFakeTimers();
    mount();
    message("onReady");
    trigger();
    expect(attention()).not.toBeNull();
    expect(info()).toHaveAccessibleName("Playback or sign-in trouble? (needs attention)");
    fireEvent.click(info());
    expect(screen.getByRole("button", { name: "Retry player" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Play on YouTube" })).toHaveAttribute("href", stream.watch_url);
  });

  it("reaches the action row by keyboard and returns focus to the button on Escape", async () => {
    const user = userEvent.setup();
    mount();
    info().focus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Use YouTube sign-in" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Retry player" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(info()).toHaveFocus();
    expect(info()).toHaveAttribute("aria-expanded", "false");
  });

  it("toggles after hover then click (touch), never sticks, and an outside tap closes it", () => {
    mount();
    fireEvent.mouseEnter(info());
    fireEvent.click(info());
    expect(info()).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(info());
    expect(info()).toHaveAttribute("aria-expanded", "false");
    fireEvent.mouseEnter(info());
    fireEvent.click(info());
    expect(info()).toHaveAttribute("aria-expanded", "true");
    fireEvent.pointerDown(document.body);
    expect(info()).toHaveAttribute("aria-expanded", "false");
  });
});
