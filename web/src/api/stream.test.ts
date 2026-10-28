import { afterEach, expect, it, vi } from "vitest";
import { stream } from "./stream";

afterEach(() => vi.unstubAllGlobals());

it("stops reading an aborted stream but leaves the server operation outcome unknown", async () => {
  sessionStorage.setItem("hz.token", "synthetic-stream-test");
  const cancel = vi.fn();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('event: task\ndata: {"text":"Installing"}\n\n'));
    },
    cancel,
  });
  const fetchMock = vi.fn().mockResolvedValue(new Response(body, { headers: { "Content-Type": "text/event-stream" } }));
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();
  const onEvent = vi.fn();
  const pending = stream("/system/plugin/add", { confirm: true }, onEvent, controller.signal).catch(
    (error: unknown) => error,
  );
  await vi.waitFor(() => expect(onEvent).toHaveBeenCalledOnce());
  controller.abort();

  expect(await pending).toMatchObject({ name: "AbortError" });
  const request = fetchMock.mock.calls[0]?.[0];
  expect(request).toBeInstanceOf(Request);
  if (!(request instanceof Request)) throw new Error("Missing stream request");
  expect(request.signal.aborted).toBe(true);
  expect(cancel).toHaveBeenCalledOnce();
});

it("parses CRLF event boundaries split across network chunks", async () => {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      for (const part of [
        'event: task\r\ndata: {"text":"Installing"}\r',
        "\n\r",
        '\nevent: result\r\ndata: {"data":{"done":true}}\r\n\r',
        "\n",
      ])
        controller.enqueue(encoder.encode(part));
      controller.close();
    },
  });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body)));
  const onEvent = vi.fn();
  await expect(stream("/system/plugin/add", {}, onEvent)).resolves.toEqual({ done: true });
  expect(onEvent).toHaveBeenCalledExactlyOnceWith({ event: "task", data: { text: "Installing" } });
});
