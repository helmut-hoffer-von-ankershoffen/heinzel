import assert from "node:assert/strict";
import test from "node:test";
import { createAboutHandler, createAboutLinkHandler, createLifecycleHandler } from "../src/lifecycle-ipc.js";

const origin = "http://127.0.0.1:30123";
const connection = () => ({ origin, token: "synthetic_native_ui_test_token_1234567890" });

test("only the live owning window's main frame can read lifecycle state", async () => {
  const frame = { isDestroyed: () => false, processId: 1, frameToken: "main", detached: false, url: `${origin}/` };
  const sender = { mainFrame: frame, isDestroyed: () => false };
  const window = { isDestroyed: () => false };
  const windows = new WeakSet([window]);
  let resolved = window;
  let reads = 0;
  const handler = createLifecycleHandler({
    windows, fromWebContents: () => resolved,
    connection,
    lifecycle: async (connection) => { reads++; return connection; },
  });
  const event = { sender, senderFrame: frame };
  assert.equal((await handler(event)).origin, origin);
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

test("About details require the owned window and its local main frame", async () => {
  const frame = { isDestroyed: () => false, processId: 1, frameToken: "main", detached: false, url: `${origin}/` };
  const sender = { mainFrame: frame, isDestroyed: () => false };
  const window = { isDestroyed: () => false };
  const unrelatedWindow = { isDestroyed: () => false };
  const details = { version: "1.2.3", path: "/Applications/Heinzelmaennchen.app" };
  let reads = 0;
  const handler = createAboutHandler({
    windows: new WeakSet([window]),
    fromWebContents: (contents) => contents === sender ? window : unrelatedWindow,
    connection,
    about: () => {
      reads++;
      return details;
    },
  });
  const event = { sender, senderFrame: frame };

  assert.deepEqual(await handler(event), details);
  for (const [description, denied] of [
    ["unrelated window on the owned origin", { ...event, sender: { ...sender } }],
    ["subframe on the owned origin", { ...event, senderFrame: { ...frame, frameToken: "child" } }],
    ["stale frame in a different process", { ...event, senderFrame: { ...frame, processId: 2 } }],
    ["detached frame", { ...event, senderFrame: { ...frame, detached: true } }],
    ["foreign origin", { ...event, senderFrame: { ...frame, url: "https://example.com/" } }],
    ["different local service", { ...event, senderFrame: { ...frame, url: "http://127.0.0.1:30124/" } }],
    ["opaque origin", { ...event, senderFrame: { ...frame, url: "data:text/html,other" } }],
  ]) {
    await assert.rejects(handler(denied), { message: "Unknown window or frame." }, description);
  }
  assert.equal(reads, 1);
});

test("About links require the owned main frame and log bounded outcomes", async () => {
  const frame = { isDestroyed: () => false, processId: 1, frameToken: "main", detached: false, url: `${origin}/` };
  const sender = { mainFrame: frame, isDestroyed: () => false };
  const window = { isDestroyed: () => false };
  const opened = [], events = [];
  let fail = false;
  const handler = createAboutLinkHandler({
    windows: new WeakSet([window]), fromWebContents: (contents) => contents === sender ? window : null,
    connection,
    open: async (url) => { if (fail) throw new Error("browser unavailable"); opened.push(url); },
    log: (event) => events.push(event),
  });
  const event = { sender, senderFrame: frame };
  for (const denied of [
    { ...event, sender: { ...sender } },
    { ...event, senderFrame: { ...frame, frameToken: "child" } },
    { ...event, senderFrame: { ...frame, processId: 2 } },
    { ...event, senderFrame: { ...frame, detached: true } },
    { ...event, senderFrame: { ...frame, url: "https://example.com/" } },
    { ...event, senderFrame: { ...frame, url: "http://127.0.0.1:30124/" } },
  ]) await assert.rejects(handler(denied, "repository"), /Unknown window or frame/);
  assert.equal(await handler(event, "release", "0.9.38"), true);
  assert.equal(await handler(event, "release", "0.9.38?token=secret"), false);
  fail = true;
  assert.equal(await handler(event, "repository"), false);
  assert.deepEqual(opened, ["https://github.com/helmut-hoffer-von-ankershoffen/heinzel/releases/tag/v0.9.38"]);
  assert.deepEqual(events.map(({ outcome }) => outcome), ["start", "opened", "start", "blocked", "start", "failed"]);
  assert.ok(events.every((entry) => Object.keys(entry).sort().join() === "action,event,outcome"));
});
