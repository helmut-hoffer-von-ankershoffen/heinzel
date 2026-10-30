import assert from "node:assert/strict";
import test from "node:test";
import { createLifecycleHandler } from "../src/lifecycle-ipc.js";

test("only the live owning window's main frame can read lifecycle state", async () => {
  const frame = { isDestroyed: () => false, processId: 1, frameToken: "main", detached: false, url: "http://127.0.0.1:30123/" };
  const sender = { mainFrame: frame, isDestroyed: () => false };
  const window = { isDestroyed: () => false };
  const windows = new WeakSet([window]);
  let resolved = window;
  let reads = 0;
  const handler = createLifecycleHandler({
    windows, fromWebContents: () => resolved,
    connection: () => ({ origin: "http://127.0.0.1:30123", token: "synthetic_native_ui_test_token_1234567890" }),
    lifecycle: async (connection) => { reads++; return connection; },
  });
  const event = { sender, senderFrame: frame };
  assert.equal((await handler(event)).origin, "http://127.0.0.1:30123");
  for (const bad of [
    { ...event, senderFrame: { ...frame, frameToken: "child" } },
    { ...event, senderFrame: { ...frame, processId: 2 } },
    { ...event, senderFrame: { ...frame, detached: true } },
    { ...event, senderFrame: null },
    { ...event, senderFrame: { ...frame, isDestroyed: () => true } },
    { ...event, senderFrame: { ...frame, url: "https://example.com/" } },
    { ...event, senderFrame: { ...frame, url: "data:text/html,other" } },
    { ...event, sender: { ...sender, isDestroyed: () => true } },
  ]) await assert.rejects(handler(bad));
  resolved = { isDestroyed: () => false };
  await assert.rejects(handler(event));
  resolved = null;
  await assert.rejects(handler(event));
  resolved = window;
  window.isDestroyed = () => true;
  await assert.rejects(handler(event));
  assert.equal(reads, 1);
});
