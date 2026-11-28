import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SchemaSoundscapeView } from "@/api/client";
import { registerSlot, resetDock, setDocked } from "./dock";
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
  resetDock();
});

describe("desktop player guest", () => {
  it("resizes a floating player when the sidebar has no slot", () => {
    render(<StreamPlayer soundscape={soundscape} stream={stream} level={{ gain: 0.5, seconds: 0, playing: true }} />);
    const slider = screen.getByRole("slider", { name: "Player width" });
    fireEvent.change(slider, { target: { value: "371" } });
    expect(slider.closest("section")).toHaveStyle({ width: "371px" });
  });

  it.each([false, true])(
    "keeps the sub-200 floating provider and fallback keyboard reachable (guest=%s)",
    async (native) => {
      const player = vi.fn(async (_request: Record<string, unknown>) => ({
        status: "ready",
        ready: true,
        state: 1,
        error: false,
      }));
      if (native) desktop(player);
      const width = window.innerWidth;
      const slot = document.createElement("div");
      document.body.append(slot);
      act(() => registerSlot(slot));
      const view = render(
        <StreamPlayer soundscape={soundscape} stream={stream} level={{ gain: 0.5, seconds: 0, playing: true }} />,
      );
      const provider = native ? screen.getByRole("img", { name: "FM player" }) : screen.getByTitle("FM player");
      vi.spyOn(provider, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 100, 200, 200));
      const card = provider.closest("section") as HTMLElement;
      if (native) await waitFor(() => expect(player).toHaveBeenCalledWith({ action: "play" }));
      try {
        act(() => setDocked(false));
        for (const available of [199, 160, 96]) {
          Object.defineProperty(window, "innerWidth", { configurable: true, value: available });
          fireEvent(window, new Event("resize"));
          expect(card).toHaveStyle({ width: `${available}px`, overflow: "auto" });
          expect(card.firstElementChild).toHaveStyle({ minWidth: "200px" });
          expect(provider).toHaveClass("min-h-[200px]", "min-w-[200px]");
          expect(card).toHaveAttribute("tabindex", "0");
          card.focus();
          expect(card).toHaveFocus();
          fireEvent.keyDown(card, { key: "Home" });
          fireEvent.keyDown(card, { key: "ArrowRight" });
          expect(card.scrollLeft).toBe(16);
          fireEvent.keyDown(card, { key: "ArrowLeft" });
          expect(card.scrollLeft).toBe(0);
          Object.defineProperty(card, "scrollWidth", { configurable: true, value: 200 });
          fireEvent.keyDown(card, { key: "End" });
          expect(card.scrollLeft).toBe(200);
          const help = screen.getByRole("button", { name: "Playback or sign-in trouble?" });
          act(() => help.focus());
          fireEvent.keyDown(help, { key: "Tab" });
          expect(
            screen.getByRole("button", { name: native ? "Copy YouTube link" : "Use YouTube sign-in" }),
          ).toHaveFocus();
          fireEvent.keyDown(help, { key: "Escape" });
          expect(provider.isConnected).toBe(true);
        }
        act(() => setDocked(true));
        expect(card).toHaveAttribute("data-dock", "docked");
        expect(card).not.toHaveAttribute("tabindex");
        expect((card.firstElementChild as HTMLElement).style.length).toBe(0);
        act(() => setDocked(false));
        expect(card).toHaveAttribute("tabindex", "0");
        if (native) {
          // A native guest cannot be clipped like an iframe. The bridge hides it, leaving header recovery controls.
          expect(player).toHaveBeenCalledWith({ action: "hide" });
          expect(player.mock.calls.filter(([request]) => request.action === "open")).toHaveLength(1);
          expect(player).not.toHaveBeenCalledWith({ action: "pause" });
          expect(player).not.toHaveBeenCalledWith({ action: "close" });
          expect(document.querySelectorAll("iframe")).toHaveLength(0);
        } else expect(document.querySelectorAll("iframe")).toHaveLength(1);
        Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
        fireEvent(window, new Event("resize"));
        expect(card).not.toHaveAttribute("tabindex");
        act(() => setDocked(true));
        expect(provider.closest("section")).toBe(card);
        expect((card.firstElementChild as HTMLElement).style.length).toBe(0);
      } finally {
        Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
        view.unmount();
        act(() => registerSlot(null));
        slot.remove();
      }
    },
  );

  it("retains the guest and its controls through expansion and return", async () => {
    const player = vi.fn(async (_request: Record<string, unknown>) => ({
      status: "ready",
      ready: true,
      state: 1,
      error: false,
    }));
    desktop(player);
    const slot = document.createElement("div");
    document.body.append(slot);
    act(() => registerSlot(slot));
    const view = render(
      <StreamPlayer soundscape={soundscape} stream={stream} level={{ gain: 0.5, seconds: 0, playing: true }} />,
    );
    const surface = screen.getByRole("img", { name: "FM player" });
    const controls = screen.getByRole("button", { name: "Playback or sign-in trouble?" });
    await waitFor(() => expect(player).toHaveBeenCalledWith({ action: "play" }));
    act(() => setDocked(false));
    const originalWidth = window.innerWidth;
    for (const width of [200, 201, 217, 390]) {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
      fireEvent(window, new Event("resize"));
      expect(screen.getByRole("img", { name: "FM player" })).toBe(surface);
    }
    Object.defineProperty(window, "innerWidth", { configurable: true, value: originalWidth });
    act(() => setDocked(true));
    expect(screen.getByRole("img", { name: "FM player" })).toBe(surface);
    expect(screen.getByRole("button", { name: "Playback or sign-in trouble?" })).toBe(controls);
    expect(player.mock.calls.filter(([request]) => request.action === "open")).toHaveLength(1);
    expect(player).not.toHaveBeenCalledWith({ action: "close" });
    expect(player).not.toHaveBeenCalledWith({ action: "pause" });
    view.unmount();
    act(() => registerSlot(null));
    slot.remove();
  });

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

  it("keeps playback help outside the native guest surface", async () => {
    const player = vi.fn(async () => ({ status: "ready", ready: true, state: 1, error: false }));
    desktop(player);
    render(<StreamPlayer soundscape={soundscape} stream={stream} level={{ gain: 0, seconds: 0, playing: false }} />);
    const help = screen.getByRole("button", { name: "Playback or sign-in trouble?" });
    expect(help.closest("header")).not.toBeNull();
    expect(screen.getByRole("img", { name: "FM player" }).contains(help)).toBe(false);
    fireEvent.click(help);
    const pane = screen.getByRole("region", { name: "Playback or sign-in trouble?" });
    expect(pane).toBeVisible();
    const header = help.closest("header") as HTMLElement;
    const capture = vi.fn();
    header.setPointerCapture = capture;
    fireEvent.pointerDown(pane.querySelector("p") as HTMLElement, { pointerId: 1 });
    expect(capture).not.toHaveBeenCalled();
    await waitFor(() => expect(player).toHaveBeenCalledWith({ action: "hide" }));
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
