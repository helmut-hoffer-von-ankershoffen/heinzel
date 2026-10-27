import assert from "node:assert/strict";
import test from "node:test";
import { createLifecycleHandler } from "../src/lifecycle-ipc.js";

test("only the live owning window's main frame can read lifecycle state", async () => {
  const frame = { isDestroyed: () => false, processId: 1, frameToken: "main", detached: false };
  const sender = { mainFrame: frame, isDestroyed: () => false };
  const window = { isDestroyed: () => false };
  const windows = new WeakSet([window]);
  let resolved = window;
  let reads = 0;
  const handler = createLifecycleHandler({
    windows, fromWebContents: () => resolved, connection: () => "private connection",
    lifecycle: async (connection) => { reads++; return connection; },
  });
  const event = { sender, senderFrame: frame };
  assert.equal(await handler(event), "private connection");
  for (const bad of [
    { ...event, senderFrame: { ...frame, frameToken: "child" } },
    { ...event, senderFrame: { ...frame, processId: 2 } },
    { ...event, senderFrame: { ...frame, detached: true } },
    { ...event, senderFrame: null },
    { ...event, senderFrame: { ...frame, isDestroyed: () => true } },
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
