import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SchemaSoundscapeView } from "@/api/client";
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
  localStorage.clear();
  resetSound();
});

describe("stream recovery", () => {
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

describe("playback help pane", () => {
  it("takes no line of the card: the help is only an icon button in the header until it is opened", () => {
    mount();
    expect(info()).toHaveAttribute("aria-expanded", "false");
    expect(info()).toHaveAttribute("title", "Playback or sign-in trouble?");
    expect(info().closest("header")).not.toBeNull();
    expect(screen.queryByText("YouTube asking you to sign in?")).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry player" })).toBeNull();
  });

  it("opens on hover, focus and tap, and closes on Escape, keeping the action row under a divider", () => {
    mount();
    fireEvent.mouseEnter(info());
    const pane = screen.getByRole("region", { name: "Playback or sign-in trouble?" });
    expect(pane).toHaveTextContent("Try the regular player");
    expect(screen.getByRole("link", { name: "Play on YouTube" }).parentElement).toHaveClass("border-t");
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
    expect(info()).toHaveAttribute("aria-expanded", "false");
  });
});
