import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fakeMedia } from "@/test/fakeMedia";
import { type Hooks, Playlist, type Track } from "./playlist";

const HOST = "https://media.heinzel.team";
const tracks: Track[] = ["one", "two", "three"].map((name) => ({
  title: `Tone ${name}`,
  url: `${HOST}/fixture/${name}.mp3`,
  type: "audio/mpeg",
  duration_seconds: 2,
}));

let media: ReturnType<typeof fakeMedia>;
let audio: HTMLAudioElement;
let hooks: { [K in keyof Hooks]: ReturnType<typeof vi.fn<Hooks[K]>> };
let playlist: Playlist;

function create({ list = tracks, deviceVolume = false } = {}) {
  playlist = new Playlist(audio, "Fixture album", list, hooks, deviceVolume);
  return playlist;
}

beforeEach(() => {
  media = fakeMedia();
  audio = document.createElement("audio");
  hooks = { changed: vi.fn(), paused: vi.fn(), resumed: vi.fn() };
});
afterEach(() => {
  playlist.dispose();
  media.restore();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Playlist", () => {
  it("loads the first track without preloading or playing it, and sends no credentials mode of its own", () => {
    create();
    expect(audio.src).toBe(tracks[0]?.url);
    expect(audio.preload).toBe("none");
    expect(audio.hasAttribute("crossorigin")).toBe(false);
    expect(media.play).not.toHaveBeenCalled();
  });

  it("starts the next track inside `ended`, in order, and starts over after the last", () => {
    create().level(0.5, 0, true);
    for (const index of [1, 2, 0]) {
      media.play.mockClear();
      media.end(audio);
      // No render, timer or promise lies between the two: a locked phone runs none of them.
      expect(audio.src).toBe(tracks[index]?.url);
      expect(media.play).toHaveBeenCalledTimes(1);
    }
    expect(hooks.paused).not.toHaveBeenCalled();
  });

  it("repeats an album of one track", () => {
    create({ list: tracks.slice(0, 1) }).level(1, 0, true);
    media.play.mockClear();
    media.end(audio);
    expect(audio.src).toBe(tracks[0]?.url);
    expect(media.play).toHaveBeenCalledTimes(1);
  });

  it("doesn't start the next track when one ends while it fades out for Pause", () => {
    vi.useFakeTimers();
    create().level(1, 0, true);
    vi.advanceTimersByTime(100);
    playlist.level(0, 4, false);
    media.play.mockClear();
    media.end(audio);
    expect(audio.src).toBe(tracks[1]?.url);
    expect(media.play).not.toHaveBeenCalled();
  });

  it("fades by the clock and pauses only once silent", () => {
    vi.useFakeTimers();
    create().level(0.8, 1, true);
    expect(audio.volume).toBe(0);
    vi.advanceTimersByTime(500);
    expect(audio.volume).toBeCloseTo(0.4);
    vi.advanceTimersByTime(500);
    expect(audio.volume).toBeCloseTo(0.8);
    playlist.level(0, 1, false);
    vi.advanceTimersByTime(500);
    expect(media.pause).not.toHaveBeenCalled();
    vi.advanceTimersByTime(500);
    expect(audio.volume).toBe(0);
    expect(audio.paused).toBe(true);
  });

  it("keeps playing silently for a meeting", () => {
    vi.useFakeTimers();
    create().level(0.5, 0, true);
    vi.advanceTimersByTime(100);
    playlist.level(0, 0, true);
    vi.advanceTimersByTime(100);
    expect(audio.volume).toBe(0);
    expect(audio.paused).toBe(false);
  });

  it("mutes instead of fading where the device owns the volume, and pauses at once", () => {
    create({ deviceVolume: true }).level(1, 4, true);
    expect(audio.volume).toBe(1);
    expect(audio.muted).toBe(false);
    playlist.level(0, 4, true);
    expect(audio.muted).toBe(true);
    expect(audio.paused).toBe(false);
    playlist.level(0, 4, false);
    expect(audio.paused).toBe(true);
    playlist.level(1, 4, true);
    expect(audio.muted).toBe(false);
    expect(audio.paused).toBe(false);
  });

  it("names the track to the system, with this app's own picture", () => {
    create();
    expect(media.session.metadata).toMatchObject({ title: "Tone one", album: "Fixture album" });
    expect(media.session.metadata?.artwork.map((art) => art.src)).toEqual(["/icons/icon-192.png"]);
    playlist.next();
    expect(media.session.metadata?.title).toBe("Tone two");
  });

  it("takes play, pause, next and previous from the lock screen", () => {
    create().level(1, 0, true);
    expect([...media.handlers.keys()].sort()).toEqual(["nexttrack", "pause", "play", "previoustrack"]);
    media.press("pause");
    expect(audio.paused).toBe(true);
    expect(hooks.paused).toHaveBeenCalledTimes(1);
    expect(media.session.playbackState).toBe("paused");
    media.press("play");
    expect(audio.paused).toBe(false);
    expect(hooks.resumed).toHaveBeenCalledTimes(1);
    expect(media.session.playbackState).toBe("playing");
    media.press("nexttrack");
    expect(audio.src).toBe(tracks[1]?.url);
    expect(audio.paused).toBe(false);
    media.press("previoustrack");
    expect(audio.src).toBe(tracks[0]?.url);
    media.press("previoustrack");
    expect(audio.src).toBe(tracks[2]?.url);
  });

  it("restarts a track that is under way on Previous", () => {
    create().level(1, 0, true);
    audio.currentTime = 30;
    playlist.previous();
    expect(audio.src).toBe(tracks[0]?.url);
    expect(audio.currentTime).toBe(0);
  });

  it("works where an action isn't supported", () => {
    media.session.setActionHandler.mockImplementation(() => {
      throw new TypeError("unsupported");
    });
    expect(() => create()).not.toThrow();
  });

  it("works without Media Session", () => {
    media.withoutSession();
    create().level(1, 0, true);
    media.end(audio);
    expect(audio.src).toBe(tracks[1]?.url);
  });

  it("follows a pause of the system's", () => {
    create().level(1, 0, true);
    media.interrupt(audio);
    expect(hooks.paused).toHaveBeenCalledTimes(1);
    expect(media.session.playbackState).toBe("paused");
  });

  it("says a track didn't load, stops, and Try again takes up where it was", () => {
    create().level(1, 0, true);
    media.fail(audio, 42);
    expect(hooks.changed).toHaveBeenLastCalledWith({ index: 0, problem: "failed" });
    expect(hooks.paused).toHaveBeenCalledTimes(1);
    expect(audio.src).toBe(tracks[0]?.url);
    playlist.retry();
    expect(audio.currentTime).toBe(42);
    expect(audio.paused).toBe(false);
    expect(hooks.changed).toHaveBeenLastCalledWith({ index: 0, problem: null });
  });

  it("loads a failed track again when Play is pressed", () => {
    create().level(1, 0, true);
    media.fail(audio, 9);
    media.press("play");
    expect(audio.currentTime).toBe(9);
    expect(hooks.changed).toHaveBeenLastCalledWith({ index: 0, problem: null });
  });

  it.each([
    ["media-src", HOST, "blocked"],
    ["media-src", "https://elsewhere.example", null],
    ["img-src", HOST, null],
  ])("reads a %s refusal of %s as %s", (effectiveDirective, blockedURI, problem) => {
    create().level(1, 0, true);
    const event = new Event("securitypolicyviolation");
    Object.assign(event, { effectiveDirective, blockedURI });
    document.dispatchEvent(event);
    if (problem) media.fail(audio); // the browser's `error` that follows keeps the reason
    expect(hooks.changed).toHaveBeenLastCalledWith({ index: 0, problem });
  });

  it("says a refused redirect didn't load: the page's policy already admits the host, so no reload helps", () => {
    create().level(1, 0, true);
    const event = new Event("securitypolicyviolation");
    Object.assign(event, {
      effectiveDirective: "media-src",
      blockedURI: tracks[0]?.url,
      originalPolicy: `default-src 'none'; media-src ${HOST}`,
    });
    document.dispatchEvent(event);
    expect(hooks.changed).toHaveBeenLastCalledWith({ index: 0, problem: "failed" });
  });

  it("says paused when the browser refuses to play without a gesture", async () => {
    media.play.mockImplementationOnce(() =>
      Promise.reject(Object.assign(new Error("no"), { name: "NotAllowedError" })),
    );
    create().level(1, 0, true);
    await vi.waitFor(() => expect(hooks.paused).toHaveBeenCalledTimes(1));
  });

  it("isn't paused by a play that a newer source interrupted", async () => {
    media.play.mockImplementationOnce(() => Promise.reject(Object.assign(new Error("new"), { name: "AbortError" })));
    create().level(1, 0, true);
    await Promise.resolve();
    await Promise.resolve();
    expect(hooks.paused).not.toHaveBeenCalled();
  });

  it("won't claim to play a track the page's policy refuses", () => {
    create().level(1, 0, true);
    const event = new Event("securitypolicyviolation");
    Object.assign(event, { effectiveDirective: "media-src", blockedURI: HOST });
    document.dispatchEvent(event);
    media.play.mockClear();
    hooks.paused.mockClear();
    media.press("play");
    expect(media.play).not.toHaveBeenCalled();
    expect(hooks.resumed).not.toHaveBeenCalled();
    expect(hooks.paused).toHaveBeenCalled();
    playlist.level(1, 0, true); // Play of the picker or the rail
    expect(media.play).not.toHaveBeenCalled();
    expect(hooks.changed).toHaveBeenLastCalledWith({ index: 0, problem: "blocked" });
  });

  it("follows the system when it resumes what it had paused", () => {
    create().level(1, 0, true);
    media.interrupt(audio);
    media.resume(audio);
    expect(hooks.resumed).toHaveBeenCalledTimes(1);
    media.end(audio);
    expect(audio.src).toBe(tracks[1]?.url);
    expect(audio.paused).toBe(false);
  });

  it.each([false, true])("still follows that resume once the app showed paused (device volume %s)", (deviceVolume) => {
    create({ deviceVolume }).level(1, 0, true);
    hooks.paused.mockImplementation(() => playlist.level(0, 0, false)); // the app renders the pause at once
    media.interrupt(audio);
    media.resume(audio);
    expect(hooks.resumed).toHaveBeenCalledTimes(1);
  });

  it("doesn't mute for Pause where the device owns the volume, so the lock screen keeps its Play", () => {
    create({ deviceVolume: true }).level(1, 0, true);
    playlist.level(0, 0, false);
    expect(audio.paused).toBe(true);
    expect(audio.muted).toBe(false);
  });

  it("lets go of the connection and the lock screen when closed", () => {
    create().level(1, 0, true);
    playlist.dispose();
    expect(audio.paused).toBe(true);
    expect(audio.hasAttribute("src")).toBe(false);
    expect(media.load).toHaveBeenCalled();
    expect(media.handlers.size).toBe(0);
    expect(media.session.metadata).toBeNull();
    expect(media.session.playbackState).toBe("none");
  });
});
