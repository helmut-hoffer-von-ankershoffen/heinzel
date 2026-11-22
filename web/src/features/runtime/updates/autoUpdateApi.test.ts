import { afterEach, expect, it, vi } from "vitest";
import { call } from "./autoUpdateApi";

afterEach(() => vi.useRealTimers());

it("cancels a pending status read with its owner and clears the recovery timer", async () => {
  vi.useFakeTimers();
  const owner = new AbortController();
  const reason = new DOMException("The page closed.", "AbortError");
  let sent: Request | undefined;
  vi.stubGlobal("fetch", (request: Request) => {
    sent = request;
    return new Promise((_resolve, reject) => {
      request.signal.addEventListener("abort", () => reject(request.signal.reason), { once: true });
    });
  });
  const result = expect(call("GET", "", owner.signal)).rejects.toBe(reason);
  owner.abort(reason);
  await result;
  expect(sent?.signal.aborted).toBe(true);
  expect(vi.getTimerCount()).toBe(0);
});

it("reports a prompt refusal without delaying it or starting a second read", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn(async () => new Response(null, { status: 401 }));
  vi.stubGlobal("fetch", fetch);
  await expect(call("GET", "")).rejects.toThrow("Automatic updates answered 401.");
  expect(fetch).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
});

it("recovers a stalled body through another read, cancelling it only after a successful answer", async () => {
  vi.useFakeTimers();
  const status = { version: "1.1.0", phase: "idle" };
  const sent: Request[] = [];
  let answer: ((response: Response) => void) | undefined;
  vi.stubGlobal("fetch", async (request: Request) => {
    sent.push(request);
    if (sent.length === 2)
      return new Promise<Response>((resolve) => {
        answer = resolve;
      });
    return new Response(
      new ReadableStream({
        start(controller) {
          request.signal.addEventListener("abort", () => controller.error(request.signal.reason), { once: true });
        },
      }),
    );
  });
  const result = call("GET", "");
  await vi.advanceTimersByTimeAsync(4_000);
  expect(sent).toHaveLength(2);
  expect(sent[0]?.signal.aborted).toBe(false);
  answer?.(Response.json(status));
  expect(await result).toEqual(status);
  expect(sent[0]?.signal.aborted).toBe(true);
  expect(vi.getTimerCount()).toBe(0);
});

it("preserves a five-second original response even when the recovery read fails", async () => {
  vi.useFakeTimers();
  const status = { version: "1.1.0", phase: "idle" };
  const sent: Request[] = [];
  vi.stubGlobal("fetch", (request: Request) => {
    sent.push(request);
    if (sent.length === 2) return Promise.resolve(new Response(null, { status: 503 }));
    return new Promise((resolve) => setTimeout(() => resolve(Response.json(status)), 5_000));
  });
  const result = call("GET", "");
  await vi.advanceTimersByTimeAsync(4_000);
  expect(sent).toHaveLength(2);
  expect(sent[0]?.signal.aborted).toBe(false);
  await vi.advanceTimersByTimeAsync(1_000);
  expect(await result).toEqual(status);
  expect(vi.getTimerCount()).toBe(0);
});

it.each(["/postpone", "/dismiss"])("keeps a slow POST %s unchanged without cancellation or replay", async (path) => {
  vi.useFakeTimers();
  const status = { version: "1.1.0", phase: "waiting", postponements: 1 };
  const sent: Request[] = [];
  vi.stubGlobal("fetch", (request: Request) => {
    sent.push(request);
    return new Promise((resolve) => setTimeout(() => resolve(Response.json(status)), 5_000));
  });
  const result = call("POST", path);
  await vi.advanceTimersByTimeAsync(5_000);
  expect(await result).toEqual(status);
  expect(sent).toHaveLength(1);
  expect(sent[0]?.method).toBe("POST");
  expect(sent[0]?.signal.aborted).toBe(false);
});

it("limits stalled status recovery to two reads and cancels both with their owner", async () => {
  vi.useFakeTimers();
  const owner = new AbortController();
  const sent: Request[] = [];
  vi.stubGlobal("fetch", (request: Request) => {
    sent.push(request);
    return new Promise((_resolve, reject) => {
      request.signal.addEventListener("abort", () => reject(request.signal.reason), { once: true });
    });
  });
  const result = expect(call("GET", "", owner.signal)).rejects.toMatchObject({ name: "AbortError" });
  await vi.advanceTimersByTimeAsync(30_000);
  expect(sent).toHaveLength(2);
  owner.abort();
  await result;
  expect(sent.every((request) => request.signal.aborted)).toBe(true);
  expect(vi.getTimerCount()).toBe(0);
});
