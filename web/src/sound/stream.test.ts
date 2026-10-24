import { describe, expect, it, vi } from "vitest";
import { command, embedSrc, playerEvent, type StreamView } from "./stream";

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
      autoplay: "1",
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
    expect(playerEvent(message('{"event":"onReady"}'), frame, ORIGIN)).toBe("onReady");
    expect(playerEvent(message('{"event":"onReady"}', "https://evil.example"), frame, ORIGIN)).toBeNull();
    expect(playerEvent(message('{"event":"onReady"}', ORIGIN, {}), frame, ORIGIN)).toBeNull();
    expect(playerEvent(message("<img onerror=alert(1)>"), frame, ORIGIN)).toBeNull();
    expect(playerEvent(message('{"info":1}'), frame, ORIGIN)).toBeNull();
    expect(playerEvent(message('{"event":"onReady"}'), null, ORIGIN)).toBeNull();
  });
});
