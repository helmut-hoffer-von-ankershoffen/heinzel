// A stream soundscape's player (ADR 0058): the host's own embed in a frame that exists only after Play, controlled by
// postMessage commands to that one origin. No script of the host runs in our page, and only its messages are heard.

export type StreamView = {
  provider: string;
  video: string;
  embed_url: string;
  frame_origin: string;
  watch_url: string;
};

const VIDEO = /^[A-Za-z0-9_-]{11}$/;

/** The frame's attributes besides `src`: what the host's player needs, and nothing that could leave our page. */
export const FRAME_ATTRIBUTES = {
  referrerPolicy: "strict-origin-when-cross-origin",
  sandbox: "allow-scripts allow-same-origin allow-presentation allow-popups allow-popups-to-escape-sandbox",
  allow: "autoplay; encrypted-media",
} as const;

/** The player's URL for `stream` on this page, or null if the stream isn't the embed of its own frame origin. */
export function embedSrc(stream: StreamView, origin: string = window.location.origin): string | null {
  let url: URL;
  try {
    url = new URL(stream.embed_url);
  } catch {
    return null;
  }
  if (url.origin !== stream.frame_origin || url.pathname !== `/embed/${stream.video}` || !VIDEO.test(stream.video)) {
    return null;
  }
  url.search = "";
  for (const [key, value] of Object.entries({ autoplay: "1", enablejsapi: "1", playsinline: "1", rel: "0", origin })) {
    url.searchParams.set(key, value);
  }
  return url.toString();
}

/** Send a command to the player (`setVolume`, `playVideo`, `pauseVideo`), to its origin only, never `*`. */
export function command(frame: HTMLIFrameElement | null, origin: string, func: string, args: unknown[] = []): void {
  frame?.contentWindow?.postMessage(JSON.stringify({ event: "command", func, args }), origin);
}

/** Ask the player to tell us when it is ready, so the volume can be set before it is heard. */
export function listen(frame: HTMLIFrameElement | null, origin: string): void {
  frame?.contentWindow?.postMessage(JSON.stringify({ event: "listening", id: "hz-sound", channel: "widget" }), origin);
}

/** The event of a message from our player: only from its origin and its frame, only a known shape; else null. */
export function playerEvent(message: MessageEvent, frame: HTMLIFrameElement | null, origin: string): string | null {
  if (message.origin !== origin || !frame || message.source !== frame.contentWindow) return null;
  try {
    const data: unknown = typeof message.data === "string" ? JSON.parse(message.data) : message.data;
    if (data && typeof data === "object" && "event" in data && typeof data.event === "string") return data.event;
  } catch {
    // not JSON: not a message of the player's
  }
  return null;
}
