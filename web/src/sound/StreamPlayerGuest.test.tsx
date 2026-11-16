import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
      expect(["open", "volume", "play", "pause", "state", "layout", "hide", "diagnostics"]).toContain(action);
    view.unmount();
    expect(player).toHaveBeenCalledWith({ action: "close" });
  });

  it("a player error from the guest asks for attention", async () => {
    desktop(vi.fn(async () => ({ status: "ready", ready: true, state: -1, error: true })));
    render(<StreamPlayer soundscape={soundscape} stream={stream} level={{ gain: 0.5, seconds: 0, playing: true }} />);
    await waitFor(() => expect(document.querySelector("[data-attention]")).not.toBeNull());
  });

  it("shows a retry action when the desktop guest cannot load", async () => {
    let attempts = 0;
    desktop(
      vi.fn(async (request) => {
        if (request.action === "open") attempts += 1;
        return { status: attempts > 1 ? "loading" : "unavailable", ready: false, state: -1, error: false };
      }),
    );
    render(<StreamPlayer soundscape={soundscape} stream={stream} level={{ gain: 0, seconds: 0, playing: false }} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The desktop player could not open this video.");
    fireEvent.click(screen.getByRole("button", { name: "Retry player" }));
    await waitFor(() => expect(attempts).toBe(2));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("shows the failure when opening the guest rejects", async () => {
    desktop(
      vi.fn(async (request) => {
        if (request.action === "open") throw new Error("guest failed");
        return { status: "empty", ready: false, state: -1, error: false };
      }),
    );
    render(<StreamPlayer soundscape={soundscape} stream={stream} level={{ gain: 0, seconds: 0, playing: false }} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The desktop player could not open this video.");
  });

  it("ignores a late failure from the previous video", async () => {
    let rejectOld!: (error: Error) => void;
    const oldOpen = new Promise<never>((_resolve, reject) => {
      rejectOld = reject;
    });
    const player = vi.fn((request: Record<string, unknown>) =>
      request.action === "open" && request.video === stream.video
        ? oldOpen
        : Promise.resolve({ status: "ready", ready: true, state: 1, error: false }),
    );
    desktop(player);
    const level = { gain: 0, seconds: 0, playing: false };
    const view = render(<StreamPlayer soundscape={soundscape} stream={stream} level={level} />);
    await waitFor(() => expect(player).toHaveBeenCalledWith({ action: "open", video: stream.video }));
    const next = {
      ...stream,
      video: "abcdefghijk",
      embed_url: `${ORIGIN}/embed/abcdefghijk`,
      watch_url: "https://www.youtube.com/watch?v=abcdefghijk",
    };
    view.rerender(<StreamPlayer soundscape={soundscape} stream={next} level={level} />);
    await waitFor(() => expect(player).toHaveBeenCalledWith({ action: "open", video: next.video }));
    await act(async () => {
      rejectOld(new Error("old failure"));
      await Promise.resolve();
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("ignores an older player state after a newer failure", async () => {
    let resolveOld!: (value: { status: string; ready: boolean; state: number; error: boolean }) => void;
    const oldState = new Promise<{ status: string; ready: boolean; state: number; error: boolean }>((resolve) => {
      resolveOld = resolve;
    });
    let states = 0;
    desktop(
      vi.fn(async (request) => {
        if (request.action === "state") {
          states += 1;
          if (states === 1) return oldState;
          return { status: "unavailable", ready: false, state: -1, error: false };
        }
        return { status: "ready", ready: true, state: 1, error: false };
      }),
    );
    render(<StreamPlayer soundscape={soundscape} stream={stream} level={{ gain: 0, seconds: 0, playing: false }} />);
    expect(await screen.findByRole("alert", {}, { timeout: 3000 })).toBeInTheDocument();
    await act(async () => {
      resolveOld({ status: "ready", ready: true, state: 1, error: false });
      await Promise.resolve();
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("reports a slow state even when later polls are in flight", async () => {
    let resolveFirst!: (value: { status: string; ready: boolean; state: number; error: boolean }) => void;
    const first = new Promise<{ status: string; ready: boolean; state: number; error: boolean }>((resolve) => {
      resolveFirst = resolve;
    });
    const later = new Promise<never>(() => {});
    let states = 0;
    desktop(
      vi.fn(async (request) => {
        if (request.action === "state") return ++states === 1 ? first : later;
        return { status: "ready", ready: true, state: 1, error: false };
      }),
    );
    render(<StreamPlayer soundscape={soundscape} stream={stream} level={{ gain: 0, seconds: 0, playing: false }} />);
    await waitFor(() => expect(states).toBeGreaterThanOrEqual(2), { timeout: 3000 });
    await act(async () => {
      resolveFirst({ status: "unavailable", ready: false, state: -1, error: false });
      await Promise.resolve();
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
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
