import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SchemaSoundscapeView } from "@/api/client";
import { guestRequest, StreamPlayer } from "./StreamPlayer";
import { resetSound } from "./store";

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

function desktop(player?: (request: Record<string, unknown>) => Promise<unknown>) {
  Object.defineProperty(window, "heinzelDesktop", {
    value: { lifecycle: vi.fn(), ...(player ? { player } : {}) },
    configurable: true,
  });
}

afterEach(() => {
  Reflect.deleteProperty(window, "heinzelDesktop");
  vi.restoreAllMocks();
  resetSound();
});

describe("desktop player guest", () => {
  it("opens the video in the desktop guest instead of an iframe, starts silent, then fades and pauses by bridge", async () => {
    const player = vi.fn(async (_request: Record<string, unknown>) => ({
      status: "ready",
      ready: true,
      state: 1,
      error: false,
    }));
    desktop(player);
    const level = { gain: 0.5, seconds: 0, playing: true };
    const view = render(<StreamPlayer soundscape={soundscape} stream={stream} level={level} />);
    expect(document.querySelector("iframe")).toBeNull();
    expect(screen.getByRole("img", { name: "FM player" })).toBeInTheDocument();
    await waitFor(() => expect(player).toHaveBeenCalledWith({ action: "open", video: "tRsQsTMvPNg" }));
    await waitFor(() => expect(player).toHaveBeenCalledWith({ action: "volume", value: 0 }));
    await waitFor(() => expect(player).toHaveBeenCalledWith({ action: "play" }));
    await waitFor(() => expect(player).toHaveBeenCalledWith({ action: "volume", value: 50 }));
    view.rerender(
      <StreamPlayer soundscape={soundscape} stream={stream} level={{ ...level, playing: false, gain: 0 }} />,
    );
    await waitFor(() => expect(player).toHaveBeenCalledWith({ action: "pause" }));
    // Only the narrow commands, never a URL or script.
    const actions = new Set(player.mock.calls.map(([request]) => String(request.action)));
    for (const action of actions)
      expect(["open", "volume", "play", "pause", "state", "layout", "hide"]).toContain(action);
    view.unmount();
    expect(player).toHaveBeenCalledWith({ action: "close" });
  });

  it("a player error from the guest asks for attention", async () => {
    desktop(vi.fn(async () => ({ status: "ready", ready: true, state: -1, error: true })));
    render(<StreamPlayer soundscape={soundscape} stream={stream} level={{ gain: 0.5, seconds: 0, playing: true }} />);
    await waitFor(() => expect(document.querySelector("[data-attention]")).not.toBeNull());
  });

  it("a desktop without the player bridge and the web app keep the iframe", () => {
    desktop();
    const view = render(
      <StreamPlayer soundscape={soundscape} stream={stream} level={{ gain: 0, seconds: 0, playing: false }} />,
    );
    expect(screen.getByTitle("FM player").tagName).toBe("IFRAME");
    view.unmount();
    Reflect.deleteProperty(window, "heinzelDesktop");
    render(<StreamPlayer soundscape={soundscape} stream={stream} level={{ gain: 0, seconds: 0, playing: false }} />);
    expect(screen.getByTitle("FM player").tagName).toBe("IFRAME");
  });

  it.each([
    ["playVideo", [], { action: "play" }],
    ["pauseVideo", [], { action: "pause" }],
    ["mute", [], { action: "volume", value: 0 }],
    ["setVolume", [42.6], { action: "volume", value: 43 }],
    ["setVolume", [140], { action: "volume", value: 100 }],
    ["setVolume", ["x"], null],
    ["unMute", [], null],
    ["addEventListener", ["onStateChange"], null],
  ] as const)("maps %s %j to the guest request %j", (func, args, expected) => {
    expect(guestRequest(func, [...args])).toEqual(expected);
  });
});
