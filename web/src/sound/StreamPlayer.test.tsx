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
const help = () => screen.getByText("YouTube asking you to sign in?").closest("details");
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
  it("switches to the account player only by choice, remembers it, and lets the viewer reverse it", () => {
    const { unmount } = mount();
    expect(new URL(player().src).origin).toBe(ORIGIN);
    fireEvent.click(screen.getByText("YouTube asking you to sign in?"));
    fireEvent.click(screen.getByRole("button", { name: "Use YouTube sign-in" }));
    expect(new URL(player().src).origin).toBe("https://www.youtube.com");
    expect(player().getAttribute("sandbox")).toContain("allow-storage-access-by-user-activation");
    unmount();
    mount();
    expect(new URL(player().src).origin).toBe("https://www.youtube.com");
    fireEvent.click(screen.getByText("YouTube asking you to sign in?"));
    fireEvent.click(screen.getByRole("button", { name: "Use private player" }));
    expect(new URL(player().src).origin).toBe(ORIGIN);
    expect(player().getAttribute("sandbox")).not.toContain("allow-storage-access-by-user-activation");
  });

  it("reveals recovery if the player is ready but never plays, and retry creates a fresh private frame", () => {
    vi.useFakeTimers();
    mount();
    const first = player();
    message("onReady");
    expect(help()).not.toHaveAttribute("open");
    act(() => vi.advanceTimersByTime(15_000));
    expect(help()).toHaveAttribute("open");
    fireEvent.click(screen.getByRole("button", { name: "Retry player" }));
    expect(player()).not.toBe(first);
    expect(new URL(player().src).origin).toBe(ORIGIN);
    expect(help()).not.toHaveAttribute("open");
    // A stale frame cannot confirm that the retry is playing.
    message("onStateChange", 1, first);
    act(() => vi.advanceTimersByTime(15_000));
    expect(help()).toHaveAttribute("open");
  });

  it.each([
    ["onStateChange", 1],
    ["infoDelivery", { playerState: 1 }],
  ])("cancels the recovery timeout only after %s confirms playback", (event, info) => {
    vi.useFakeTimers();
    mount();
    message(event, info);
    act(() => vi.advanceTimersByTime(15_000));
    expect(help()).not.toHaveAttribute("open");
  });

  it("does not suggest sign-in when the viewer pauses after playback started", () => {
    vi.useFakeTimers();
    mount();
    message("onStateChange", 1);
    message("onStateChange", 2);
    act(() => vi.advanceTimersByTime(15_000));
    expect(help()).not.toHaveAttribute("open");
  });

  it.each(["onError", "onAutoplayBlocked"])("reveals recovery immediately after %s", (event) => {
    mount();
    message(event, 150);
    expect(help()).toHaveAttribute("open");
  });

  it("ignores events from the old private origin after switching, and sends commands to the account origin", () => {
    rememberYouTubeAccount(true);
    mount();
    const post = vi.spyOn(player().contentWindow as Window, "postMessage");
    message("onError", 150, player(), ORIGIN);
    expect(help()).not.toHaveAttribute("open");
    message("onReady");
    expect(post).toHaveBeenCalled();
    expect(post.mock.calls.every((call) => (call as unknown[])[1] === "https://www.youtube.com")).toBe(true);
  });

  it("keeps the account choice for this player even when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    mount();
    fireEvent.click(screen.getByText("YouTube asking you to sign in?"));
    fireEvent.click(screen.getByRole("button", { name: "Use YouTube sign-in" }));
    expect(new URL(player().src).origin).toBe("https://www.youtube.com");
  });

  it.each(["Play on YouTube", "Open on YouTube"])("%s opens the actual video and stops the embedded player", (name) => {
    toggle();
    mount();
    fireEvent.click(screen.getByText("YouTube asking you to sign in?"));
    const link = screen.getByRole("link", { name });
    expect(link).toHaveAttribute("href", stream.watch_url);
    expect(link).toHaveAttribute("target", "_blank");
    fireEvent.click(link);
    expect(soundState()).toMatchObject({ playing: false, started: false });
  });
});
