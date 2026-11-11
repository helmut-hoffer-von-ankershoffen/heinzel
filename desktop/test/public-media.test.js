import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";
import { createPublicMediaHandler, publicMediaUrl } from "../src/public-media.js";

const screenshot = `https://raw.githubusercontent.com/fictional-lab/harbor/${"a".repeat(40)}/docs/ui.png`;
const sound = "https://www.youtube.com/watch?v=abcdefghijk";
const connection = { origin: "http://127.0.0.1:30123", token: "synthetic_native_ui_test_token_1234567890" };

test("only bounded canonical immutable rasters and exact watch identities may reach the browser", () => {
  for (const url of [screenshot, screenshot.replace(".png", ".jpg"), screenshot.replace(".png", ".webp"),
    screenshot.replace("docs/ui.png", `${"d".repeat(200)}/${"f".repeat(200)}.png`), sound])
    assert.equal(publicMediaUrl(url), url);
  for (const url of [null, {}, "x".repeat(1025), "file:///tmp/a.png", "javascript:alert(1)",
    "http://127.0.0.1/image.png", sound.replace("https:", "http:"), sound.replace("www.youtube.com", "www.youtube.com.evil.example"),
    sound + "&token=secret", sound + "#token=secret", sound.replace("www.youtube.com", "user:secret@www.youtube.com"),
    sound.replace("www.youtube.com", "www.youtube.com:443"), sound.replace("abcdefghijk", "bad"),
    screenshot.replace("raw.githubusercontent.com", "raw.githubusercontent.com.evil.example"),
    screenshot.replace("raw.githubusercontent.com", "user:secret@raw.githubusercontent.com"),
    screenshot.replace("a".repeat(40), "main"), screenshot.replace("docs/ui.png", "docs/../ui.png"),
    screenshot.replace("docs/ui.png", "docs//ui.png"), screenshot.replace("docs/ui.png", "docs/%2e%2e/ui.png"),
    screenshot.replace("docs/ui.png", "docs/%2f%2fevil.example/ui.png"),
    screenshot.replace("ui.png", "ui.svg"), screenshot.replace("ui.png", "%75i.png"), screenshot + "?token=secret",
    screenshot + "#secret", screenshot.replace("https:", "http:")]) assert.equal(publicMediaUrl(url), null);
});

test("only the current owning main frame hands off media and opener failures stay closed", async () => {
  const frame = { isDestroyed: () => false, processId: 1, frameToken: "main", detached: false, url: connection.origin + "/" };
  const sender = { mainFrame: frame, isDestroyed: () => false };
  const window = { isDestroyed: () => false };
  let resolved = window, selected = connection, fail = false;
  const opened = [];
  const handler = createPublicMediaHandler({ windows: new WeakSet([window]), fromWebContents: () => resolved,
    connection: () => selected, openExternal: async (url) => { if (fail) throw new Error("unavailable"); opened.push(url); } });
  const event = { sender, senderFrame: frame };
  assert.equal(await handler(event, screenshot), true);
  assert.equal(await handler(event, sound), true);
  for (const bad of [
    { ...event, senderFrame: { ...frame, frameToken: "child" } },
    { ...event, senderFrame: { ...frame, processId: 2 } },
    { ...event, senderFrame: { ...frame, detached: true } },
    { ...event, senderFrame: null },
    { ...event, senderFrame: { ...frame, isDestroyed: () => true } },
    { ...event, senderFrame: { ...frame, url: "https://example.com/" } },
    { ...event, sender: { ...sender, isDestroyed: () => true } },
    { ...event, sender: { ...sender, mainFrame: { ...frame, isDestroyed: () => true } } },
  ]) assert.equal(await handler(bad, sound), false);
  assert.equal(await handler(event, "file:///tmp/a.png"), false);
  selected = null;
  assert.equal(await handler(event, sound), false);
  selected = connection;
  resolved = {};
  assert.equal(await handler(event, sound), false);
  resolved = null;
  assert.equal(await handler(event, sound), false);
  resolved = window;
  window.isDestroyed = () => true;
  assert.equal(await handler(event, sound), false);
  window.isDestroyed = () => false;
  fail = true;
  assert.equal(await handler(event, sound), false);
  assert.deepEqual(opened, [screenshot, sound]);
});

test("the isolated preload exposes no opener and requires trusted activated primary anchor clicks", async () => {
  let click, exposed;
  const invoked = [], results = [];
  class Element {}
  class Anchor extends Element {
    href = sound;
    dispatchEvent(event) { results.push(event.detail); }
    closest() { return this; }
  }
  const activation = { isActive: true };
  const context = vm.createContext({
    require: () => ({ contextBridge: { exposeInMainWorld: (_name, api) => { exposed = api; } },
      ipcRenderer: { invoke: async (channel, url) => { invoked.push([channel, url]); return false; } } }),
    document: { addEventListener: (name, handler, capture) => { assert.equal(name, "click"); assert.equal(capture, true); click = handler; } },
    navigator: { userActivation: activation }, Element, HTMLAnchorElement: Anchor,
    CustomEvent: class { constructor(name, options) { this.name = name; this.detail = options.detail; } },
  });
  vm.runInContext(readFileSync(new URL("../src/preload.cjs", import.meta.url), "utf8"), context);
  assert.deepEqual(Object.keys(exposed), ["workspaceAudio", "titlebar", "browser", "lifecycle", "onFullscreen"]);
  const anchor = new Anchor();
  let prevented = 0;
  const event = { isTrusted: true, button: 0, target: anchor, preventDefault: () => { prevented++; } };
  click({ ...event, isTrusted: false });
  click({ ...event, button: 1 });
  click({ ...event, target: {} });
  activation.isActive = false;
  click(event);
  activation.isActive = true;
  assert.deepEqual(invoked, []);
  click(event);
  await Promise.resolve();
  assert.deepEqual(invoked, [["hz:public-media", sound]]);
  assert.equal(prevented, 1);
  assert.deepEqual(results, [false]);
});
