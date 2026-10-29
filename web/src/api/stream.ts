// Server-sent events from `POST /api/stream/<path>` (heinzel/heads/_stream.py, ADR 0045): an operation's events as it
// runs (`text`, `tool`, `task`), then `result` or `error`. Read with fetch, not EventSource: only fetch sends the
// session token as a header (ADR 0039). One reader for every stream the app gets; notifications should use it too.
import { problem } from "./client";
import { token } from "./token";

export type StreamEvent = { event: string; data: Record<string, unknown> };
type Query = Record<string, string | number | boolean | null | undefined>;

/** Split a buffer into complete events and the rest, which waits for more bytes. */
export function parseEvents(buffer: string): { events: StreamEvent[]; rest: string } {
  const blocks = buffer.split(/\r?\n\r?\n/);
  const rest = blocks.pop() ?? "";
  const events: StreamEvent[] = [];
  for (const block of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (!data.length) continue;
    try {
      events.push({ event, data: JSON.parse(data.join("\n")) as Record<string, unknown> });
    } catch {
      // Not JSON: not one of ours, skipped.
    }
  }
  return { events, rest };
}

/** Run the operation at `path` (e.g. `/system/runner/chat/send`) as a stream; resolves with its result. */
export async function stream<T>(
  path: string,
  query: Query,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<T> {
  signal?.throwIfAborted();
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) if (value != null) params.set(key, String(value));
  const headers = new Headers();
  const value = token();
  if (value) headers.set("Authorization", `Bearer ${value}`);
  let abort = () => {};
  const interrupted = new Promise<never>((_resolve, reject) => {
    abort = () => reject(signal?.reason ?? new DOMException("Stopped waiting for the operation.", "AbortError"));
    signal?.addEventListener("abort", abort, { once: true });
  });
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  try {
    const response = await Promise.race([
      globalThis
        .fetch(
          new Request(`${window.location.origin}/api/stream${path}?${params}`, { method: "POST", headers, signal }),
        )
        .then((response) => {
          if (signal?.aborted) {
            void response.body?.cancel().catch(() => {});
            throw signal.reason;
          }
          return response;
        }),
      interrupted,
    ]);
    if (!response.ok || !response.body) {
      throw problem(response.status, await Promise.race([response.json().catch(() => null), interrupted]));
    }
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value: chunk } = await Promise.race([reader.read(), interrupted]);
      buffer += decoder.decode(chunk, { stream: !done });
      const { events, rest } = parseEvents(done ? `${buffer}\n\n` : buffer);
      buffer = rest;
      for (const event of events) {
        if (event.event === "result") return event.data.data as T;
        if (event.event === "error") throw problem(400, event.data);
        onEvent(event);
      }
      if (done) throw problem(502, { error: "The stream ended without a result." });
    }
  } finally {
    signal?.removeEventListener("abort", abort);
    // Closing a reader stops waiting, not the server-side mutation. Its outcome remains uncertain on disconnect.
    void reader?.cancel().catch(() => {});
    reader?.releaseLock();
  }
}
