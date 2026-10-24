// The notice stream (ADR 0041): server-sent events read with `fetch`, because `EventSource` can't send the Bearer
// header and the token must never be in a URL. Reconnects with a growing delay; stops when its signal aborts.
import type { SchemaNotice } from "@/api/client";
import { token } from "@/api/token";
import { splash } from "@/splash/store";

export type StreamEvent = { event: "notice"; notice: SchemaNotice } | { event: "changed"; latest: string | null };

/** The events in a chunk of `text/event-stream`, and the unfinished rest to prepend to the next chunk. */
export function parseEvents(buffer: string): { events: StreamEvent[]; rest: string } {
  const blocks = buffer.split(/\r?\n\r?\n/);
  const rest = blocks.pop() ?? "";
  const events: StreamEvent[] = [];
  for (const block of blocks) {
    let name = "";
    const data: string[] = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (!data.length) continue;
    try {
      const value = JSON.parse(data.join("\n"));
      if (name === "notice") events.push({ event: "notice", notice: value as SchemaNotice });
      else if (name === "changed") events.push({ event: "changed", latest: (value?.latest as string | null) ?? null });
    } catch {
      // A damaged event is skipped; the next `changed` brings the list up to date anyway.
    }
  }
  return { events, rest };
}

const MAX_DELAY = 30_000;

/** Read the stream until `signal` aborts, calling `onEvent` for every event; `popups` claims the due notices. */
export async function follow(
  onEvent: (event: StreamEvent) => void,
  {
    popups,
    signal,
    wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  }: {
    popups: boolean;
    signal: AbortSignal;
    wait?: (ms: number) => Promise<unknown>;
  },
): Promise<void> {
  let delay = 1_000;
  while (!signal.aborted) {
    try {
      const url = `${window.location.origin}/api/system/notify/stream${popups ? "?popups=true" : ""}`;
      const value = token();
      const headers = { Accept: "text/event-stream", ...(value ? { Authorization: `Bearer ${value}` } : {}) };
      const response = await globalThis.fetch(new Request(url, { headers, signal, cache: "no-store" }));
      if (response.status === 401 || response.status === 403) return; // a stale token: the screens say what to do
      if (response.ok && response.body) {
        delay = 1_000;
        const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
        let buffer = "";
        for (;;) {
          const { value: chunk, done } = await reader.read();
          if (done) break;
          const parsed = parseEvents(buffer + chunk);
          buffer = parsed.rest;
          for (const event of parsed.events) onEvent(event);
        }
      }
    } catch {
      if (signal.aborted) return;
      splash.send({ type: "suspect" }); // the stream broke: hz may be gone, and the splash checks
    }
    await wait(delay);
    delay = Math.min(delay * 2, MAX_DELAY);
  }
}
