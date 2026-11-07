import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { createMiniBrowser } from "../src/mini-browser.js";

function fixture(enabled = true, configure = async () => {}) {
  const frame = { processId: 1, frameToken: "main", url: "http://127.0.0.1:8737/", detached: false, isDestroyed: () => false };
  const sender = Object.assign(new EventEmitter(), { mainFrame: frame, isDestroyed: () => false, getZoomFactor: () => 1 });
  const views = [], sessions = [], options = [], calls = [];
  const owner = Object.assign(new EventEmitter(), { webContents: sender, isDestroyed: () => false, isFocused: () => true,
    getContentSize: () => [1000, 700], contentView: { addChildView: (view) => calls.push(["add", view]), removeChildView: () => calls.push(["remove"]) } });
  const controller = createMiniBrowser({ createNetwork: async () => ({ configure: async () => { await configure(); calls.push(["network-ready"]); }, close: () => calls.push(["network-close"]), authenticate: (_info, callback) => callback() }), windows: new WeakSet([owner]), fromWebContents: (contents) => contents === sender ? owner : null,
    connection: () => ({ origin: "http://127.0.0.1:8737", token: "a".repeat(32) }), enabled: async () => typeof enabled === "function" ? enabled() : enabled,
    createSession: (partition, config) => {
      const session = Object.assign(new EventEmitter(), { partition, config, clearStorageData: async () => calls.push(["clear"]),
        setPermissionCheckHandler: (handler) => { session.check = handler; },
        setPermissionRequestHandler: (handler) => { session.request = handler; },
        webRequest: { onBeforeRequest: (handler) => { session.before = handler; } } });
      sessions.push(session); return session;
    }, createView: (preferences) => {
      options.push(preferences); let url = "", dead = false;
      const contents = Object.assign(new EventEmitter(), { isDestroyed: () => dead,
        close: (options) => { dead = true; calls.push(["close", options]); }, getURL: () => url,
        loadURL: async (target) => { url = target; calls.push(["load", target]); }, reload: () => calls.push(["reload"]),
        navigationHistory: { canGoBack: () => false, canGoForward: () => false },
        setWebRTCIPHandlingPolicy: (policy) => calls.push(["webrtc", policy]),
        setWindowOpenHandler: (handler) => { contents.popup = handler; } });
      const view = { webContents: contents, setBounds: (bounds) => calls.push(["bounds", bounds]), setVisible: (visible) => calls.push(["visible", visible]) };
      views.push(view); return view;
    } });
  return { controller, owner, sender, frame, views, sessions, options, calls,
    invoke: (request) => controller.handle({ sender, senderFrame: frame }, request) };
}

test("native guest is dark until explicitly enabled and never borrows the product session/preload", async () => {
  const dark = fixture(false);
  await assert.rejects(dark.invoke({ action: "navigate", url: "https://example.com" }));
  assert.equal(dark.views.length, 0);
  const f = fixture(); await f.invoke({ action: "navigate", url: "https://example.com" });
  assert.match(f.sessions[0].partition, /^hz-browser-/);
  assert.ok(!f.sessions[0].partition.startsWith("persist:"));
  assert.equal(f.options[0].webPreferences.session, f.sessions[0]);
  assert.equal(f.options[0].webPreferences.preload, undefined);
  for (const key of ["nodeIntegration", "nodeIntegrationInSubFrames", "webviewTag"])
    assert.equal(f.options[0].webPreferences[key], false);
  assert.equal(f.options[0].webPreferences.sandbox, true);
  assert.equal(f.options[0].webPreferences.contextIsolation, true);
});
test("all permissions, downloads, popup dispatch and local redirects/subresources refuse", async () => {
  const f = fixture(); await f.invoke({ action: "navigate", url: "https://example.com" });
  const session = f.sessions[0], contents = f.views[0].webContents;
  assert.equal(session.check(), false);
  session.request(null, "media", (allowed) => assert.equal(allowed, false));
  let prevented = 0;
  session.emit("will-download", { preventDefault: () => prevented++ });
  assert.deepEqual(contents.popup({ url: "https://other.example.com" }), { action: "deny" });
  contents.emit("will-redirect", { url: "http://127.0.0.1:8737", preventDefault: () => prevented++ });
  assert.equal(prevented, 2);
  for (const url of ["http://127.0.0.1:8737/api", "file:///private/data", "https://user:password@example.com"])
    session.before({ url }, (result) => assert.equal(result.cancel, true));
  session.before({ url: "https://example.com/image.png" }, (result) => assert.equal(result.cancel, false));
});
test("every redirect hop and request to a non-public target is refused, a public hop passes", async () => {
  const f = fixture(); await f.invoke({ action: "navigate", url: "https://example.com" });
  const session = f.sessions[0], contents = f.views[0].webContents;
  const hops = ["http://example.com/", "https://127.0.0.1/", "https://10.0.0.5/", "https://192.168.1.2/",
    "https://172.20.0.1/", "https://169.254.169.254/", "https://100.101.102.103/", "https://mini.tail1234.ts.net/",
    "https://mini.local/", "https://localhost/", "file:///etc/hosts", "data:text/html,a", "javascript:alert(1)",
    "blob:https://example.com/id", "http://127.0.0.1:8737/"];
  for (const url of hops) {
    for (const event of ["will-redirect", "will-navigate", "will-frame-navigate"]) {
      let prevented = false;
      contents.emit(event, { url, preventDefault: () => { prevented = true; } });
      assert.equal(prevented, true, `${event}: ${url}`);
    }
    session.before({ url }, (result) => assert.equal(result.cancel, true, url));
  }
  let prevented = false;
  contents.emit("will-redirect", { url: "https://www.example.com/next", preventDefault: () => { prevented = true; } });
  assert.equal(prevented, false);
  await assert.rejects(f.invoke({ action: "navigate", url: "https://mini.tail1234.ts.net/" }));
});
test("surface layout is bounded, hidden on request, destroyed on product navigation and session not reused", async () => {
  const f = fixture(); await f.invoke({ action: "navigate", url: "https://example.com" });
  await assert.rejects(f.invoke({ action: "layout", bounds: { x: -1, y: 0, width: 100, height: 100 } }));
  await f.invoke({ action: "layout", bounds: { x: 600, y: 100, width: 300, height: 400 } });
  assert.deepEqual(f.calls.at(-1), ["visible", true]);
  f.owner.getContentSize = () => [800, 700];
  await assert.rejects(f.invoke({ action: "layout", bounds: { x: 600, y: 100, width: 300, height: 400 } }));
  assert.deepEqual(f.calls.at(-1), ["visible", false]);
  await f.invoke({ action: "layout", bounds: { x: 500, y: 100, width: 300, height: 400 } });
  assert.deepEqual(f.calls.at(-1), ["visible", true]);
  await f.invoke({ action: "hide" });
  assert.deepEqual(f.calls.at(-1), ["visible", false]);
  f.owner.emit("blur");
  assert.deepEqual(f.calls.at(-1), ["visible", false]);
  f.sender.emit("did-start-navigation", { isMainFrame: false, isSameDocument: false });
  assert.equal(f.views[0].webContents.isDestroyed(), false);
  f.sender.emit("did-start-navigation", { isMainFrame: true, isSameDocument: false });
  assert.ok(f.calls.some(([name, options]) => name === "close" && options.waitForBeforeUnload === false));
  assert.equal(f.sender.listenerCount("did-start-navigation"), 0);
  assert.equal(f.owner.listenerCount("closed"), 0);
  assert.ok(f.calls.some(([name]) => name === "clear"));
  await f.invoke({ action: "navigate", url: "https://example.com" });
  assert.notEqual(f.sessions[0].partition, f.sessions[1].partition);
  f.controller.closeAll();
  assert.ok(f.views.every((view) => view.webContents.isDestroyed()));
});
test("unknown commands, fields and a guest sender cannot control the product", async () => {
  const f = fixture();
  for (const request of [{ action: "shell", url: "https://example.com" }, { action: "close", command: "x" }, null])
    await assert.rejects(f.invoke(request));
  await assert.rejects(f.controller.handle({ sender: { isDestroyed: () => false }, senderFrame: f.frame }, { action: "close" }));
  assert.equal(f.views.length, 0);
});
test("concurrent initial navigation creates only one guest after asynchronous feature validation", async () => {
  const f = fixture();
  await Promise.all([f.invoke({ action: "navigate", url: "https://example.com/one" }), f.invoke({ action: "navigate", url: "https://example.com/two" })]);
  assert.equal(f.views.length, 1);
});

test("closing, navigating or shutting down during feature validation cannot create a late guest", async () => {
  for (const action of ["close", "shutdown", "navigation"]) {
    let complete;
    const f = fixture(() => new Promise((resolve) => { complete = resolve; }));
    const pending = f.invoke({ action: "navigate", url: "https://example.com" });
    if (action === "shutdown") f.controller.closeAll();
    else if (action === "navigation") {
      f.sender.emit("did-start-navigation", { isMainFrame: true, isSameDocument: false });
    } else await f.invoke({ action: "close" });
    complete(true);
    await assert.rejects(pending);
    assert.equal(f.views.length, 0);
  }
});

test("guest declines platform certificates and authentication challenges", async () => {
  const f = fixture();
  await f.invoke({ action: "navigate", url: "https://example.com" });
  const contents = f.views[0].webContents;
  for (const [name, args] of [
    ["select-client-certificate", ["https://example.com", [{ fingerprint: "platform identity" }]]],
    ["login", [{}, {}]],
  ]) {
    let prevented = false;
    let declined = false;
    contents.emit(name, { preventDefault: () => { prevented = true; } }, ...args, (...values) => {
      assert.deepEqual(values, []);
      declined = true;
    });
    assert.ok(prevented && declined);
  }
  assert.ok(f.calls.some(([name, policy]) => name === "webrtc" && policy === "disable_non_proxied_udp"));
});

test("proxy setup fails closed and close during setup never loads a late website", async () => {
  const broken = fixture(true, async () => { throw new Error("private internal error"); });
  await assert.rejects(broken.invoke({ action: "navigate", url: "https://example.com" }), /^Error: Browser network is unavailable\.$/);
  assert.equal(broken.views.length, 0);
  assert(broken.calls.some(([name]) => name === "network-close"));
  let complete, started;
  const pending = new Promise(resolve => { complete = resolve; });
  const began = new Promise(resolve => { started = resolve; });
  const f = fixture(true, async () => { started(); await pending; });
  const navigation = f.invoke({ action: "navigate", url: "https://example.com" });
  await began;
  await f.invoke({ action: "close" });
  complete();
  await assert.rejects(navigation);
  assert(!f.calls.some(([name]) => name === "load"));
  assert(f.calls.some(([name]) => name === "network-close"));
  assert(f.views.every(view => view.webContents.isDestroyed()));
});
