import assert from "node:assert/strict";
import test from "node:test";
import { createContext, runInContext } from "node:vm";
import { probeWorkspaceRenderer } from "../src/workspace-attachment.js";

test("only the latest accepted recovery can notify the retained authenticated document once", async () => {
  const origin = "https://studio.example";
  let token = "session-a";
  const events = [];
  const world = createContext({
    location: { origin }, sessionStorage: { getItem: () => token }, localStorage: { getItem: () => null },
    fetch: async () => Response.json({ instance: "Studio", version: "0.9.58" }), AbortSignal, Event,
    window: { dispatchEvent: (event) => events.push([event.type, event.detail]) },
  });
  const contents = {
    mainFrame: { url: origin }, isDestroyed: () => false,
    executeJavaScriptInIsolatedWorld: async (_world, [{ code }]) => runInContext(code, world),
  };
  const probe = (signal) => probeWorkspaceRenderer({ webContents: contents }, { origin }, signal, true);
  const earlier = await probe();
  const current = await probe();
  earlier.notifyRestored();
  assert.deepEqual(events, []);
  current.notifyRestored(); current.notifyRestored();
  assert.deepEqual(events, [["hz:workspace-restored", undefined]]);

  const rotated = await probe();
  token = "session-b";
  rotated.notifyRestored();
  const paused = new AbortController();
  const inFlight = await probe(paused.signal);
  paused.abort(); inFlight.notifyRestored();
  const navigated = await probe();
  contents.mainFrame = { url: origin };
  navigated.notifyRestored();
  assert.equal(events.length, 1);
});

test("a stale launch and valid pair restore the idle document without changing page storage", async () => {
  const origin = "https://studio.example";
  let paired = "paired";
  const reads = [], events = [];
  const world = createContext({
    location: { origin }, sessionStorage: { getItem: () => "stale" }, localStorage: { getItem: () => paired },
    fetch: async (_url, options) => {
      const authority = options.headers.Authorization;
      reads.push(authority);
      return authority === "Bearer stale" ? new Response(null, { status: 401 })
        : Response.json({ instance: "Studio", version: "0.9.58" });
    },
    AbortSignal, Event, window: { dispatchEvent: (event) => events.push(event.type) },
  });
  const webContents = {
    mainFrame: { url: origin }, isDestroyed: () => false,
    executeJavaScriptInIsolatedWorld: async (_world, [{ code }]) => runInContext(code, world),
  };
  const probe = () => probeWorkspaceRenderer({ webContents }, { origin }, undefined, true);
  const result = await probe();
  assert.deepEqual(reads, ["Bearer stale", "Bearer paired"]);
  assert.equal(result.instance, "Studio");
  assert.equal(result.authoritySource, "device");
  result.notifyRestored();
  const superseded = await probe();
  paired = "new-device";
  superseded.notifyRestored();
  assert.deepEqual(events, ["hz:workspace-restored"]);
});
