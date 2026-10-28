import { afterEach, describe, expect, it, vi } from "vitest";
import {
  accountStream,
  command,
  embedSrc,
  playerEvent,
  readYouTubeAccount,
  rememberYouTubeAccount,
  type StreamView,
} from "./stream";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

const ORIGIN = "https://www.youtube-nocookie.com";
const fm: StreamView = {
  provider: "youtube",
  video: "tRsQsTMvPNg",
  embed_url: `${ORIGIN}/embed/tRsQsTMvPNg`,
  frame_origin: ORIGIN,
  watch_url: "https://www.youtube.com/watch?v=tRsQsTMvPNg",
};

describe("the stream's player URL", () => {
  it("is the privacy-enhanced embed with the JS API for our origin", () => {
    const url = new URL(embedSrc(fm, "http://127.0.0.1:8765") ?? "");
    expect(url.origin).toBe(ORIGIN);
    expect(url.pathname).toBe("/embed/tRsQsTMvPNg");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      enablejsapi: "1",
      playsinline: "1",
      rel: "0",
      origin: "http://127.0.0.1:8765",
    });
  });

  it.each([
    { embed_url: "https://www.youtube.com/embed/tRsQsTMvPNg" },
    { embed_url: "https://evil.example/embed/tRsQsTMvPNg" },
    { embed_url: `${ORIGIN}/embed/other` },
    { embed_url: "javascript:alert(1)" },
    { embed_url: "not a url" },
    { video: "tRsQ/../x12", embed_url: `${ORIGIN}/embed/tRsQ/../x12` },
  ])("is refused when it isn't the embed of its own frame origin: %o", (change) => {
    expect(embedSrc({ ...fm, ...change })).toBeNull();
  });
});

describe("talking to the player", () => {
  it("posts commands to the player's origin only, never to any origin", () => {
    const postMessage = vi.fn();
    const frame = { contentWindow: { postMessage } } as unknown as HTMLIFrameElement;
    command(frame, ORIGIN, "setVolume", [40]);
    expect(postMessage).toHaveBeenCalledWith('{"event":"command","func":"setVolume","args":[40]}', ORIGIN);
    command(null, ORIGIN, "pauseVideo"); // no frame yet: nothing to send to
  });

  it("hears only its own frame from its origin, in the player's shape", () => {
    const source = {} as Window;
    const frame = { contentWindow: source } as unknown as HTMLIFrameElement;
    const message = (data: unknown, origin = ORIGIN, from: unknown = source) =>
      ({ data, origin, source: from }) as MessageEvent;
    expect(playerEvent(message('{"event":"onReady"}'), frame, ORIGIN)).toEqual({ event: "onReady" });
    expect(playerEvent(message({ event: "onStateChange", info: 1 }), frame, ORIGIN)).toEqual({
      event: "onStateChange",
      info: 1,
    });
    expect(playerEvent(message('{"event":"onReady"}', "https://evil.example"), frame, ORIGIN)).toBeNull();
    expect(playerEvent(message('{"event":"onReady"}', ORIGIN, {}), frame, ORIGIN)).toBeNull();
    expect(playerEvent(message("<img onerror=alert(1)>"), frame, ORIGIN)).toBeNull();
    expect(playerEvent(message('{"info":1}'), frame, ORIGIN)).toBeNull();
    expect(playerEvent(message('{"event":"onReady"}'), null, ORIGIN)).toBeNull();
  });
});

describe("YouTube account playback", () => {
  it("uses only the fixed regular YouTube origin for the known private player", () => {
    expect(accountStream(fm)).toMatchObject({
      frame_origin: "https://www.youtube.com",
      embed_url: "https://www.youtube.com/embed/tRsQsTMvPNg",
      watch_url: fm.watch_url,
    });
    expect(accountStream({ ...fm, provider: "other" })).toBeNull();
    expect(accountStream({ ...fm, frame_origin: "https://evil.example" })).toBeNull();
    expect(accountStream({ ...fm, embed_url: "https://evil.example/embed/tRsQsTMvPNg" })).toBeNull();
  });

  it("remembers the explicit choice and its reversal", () => {
    expect(readYouTubeAccount()).toBe(false);
    rememberYouTubeAccount(true);
    expect(readYouTubeAccount()).toBe(true);
    rememberYouTubeAccount(false);
    expect(readYouTubeAccount()).toBe(false);
  });

  it("keeps working when browser policy denies storage", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });
    expect(readYouTubeAccount()).toBe(false);
    expect(() => rememberYouTubeAccount(true)).not.toThrow();
  });
});
