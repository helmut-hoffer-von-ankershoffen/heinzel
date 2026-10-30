import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { authenticatedUiUrl, guardUiNavigation, isOwnedUiNavigation, openProductUi } from "../src/ui-navigation.js";

const connection = Object.freeze({ origin: "http://127.0.0.1:30123", token: "synthetic_native_ui_test_token_1234567890" });

test("the product UI receives only the owned loopback fragment authentication", () => {
  const url = new URL(authenticatedUiUrl(connection));
  assert.equal(url.origin, connection.origin);
  assert.equal(url.pathname, "/");
  assert.equal(url.search, "");
  assert.equal(url.hash, `#token=${connection.token}`);
  for (const bad of [
    { ...connection, origin: "https://127.0.0.1:30123" },
    { ...connection, origin: "http://localhost:30123" },
    { ...connection, origin: "http://127.0.0.1:30123/path" },
    { ...connection, token: "short" },
    { ...connection, token: `${connection.token}&extra=value` },
  ]) assert.throws(() => authenticatedUiUrl(bad));
});

test("same-origin routes and reload work; credentials, other ports and schemes are denied", () => {
  for (const path of ["/", "/heinzel", "/?panel=chat", "/contracts/sample#section"])
    assert.equal(isOwnedUiNavigation(`${connection.origin}${path}`, connection), true);
  for (const url of [
    "https://example.com/", "http://127.0.0.1:30124/", "http://localhost:30123/",
    "http://user:pass@127.0.0.1:30123/", "data:text/html,anything", "file:///tmp/ui.html",
    "javascript:alert(1)", "http://127.0.0.1:30123.example.com/", "not a URL",
  ]) assert.equal(isOwnedUiNavigation(url, connection), false);
});

test("the actual navigation handlers block cross-origin main-frame redirects and popups", () => {
  const contents = new EventEmitter();
  let open;
  contents.setWindowOpenHandler = (handler) => { open = handler; };
  guardUiNavigation(contents, connection);
  let blocked = 0;
  const event = (url, isMainFrame = true) => ({ url, isMainFrame, preventDefault: () => { blocked++; } });
  contents.emit("will-navigate", event(`${connection.origin}/heinzel`));
  contents.emit("will-navigate", event("https://example.com/"));
  contents.emit("will-redirect", event("https://example.com/"));
  contents.emit("will-redirect", event(`${connection.origin}/profile`));
  contents.emit("will-redirect", event("https://example.com/embed", false));
  assert.equal(blocked, 2);
  assert.deepEqual(open({ url: "https://example.com/" }), { action: "deny" });
});

test("quit while lifecycle authentication is pending never constructs a window", async () => {
  let finish;
  let stopping = false;
  let created = 0;
  const opened = openProductUi({
    connection, lifecycle: () => new Promise((resolve) => { finish = resolve; }),
    createWindow: () => { created++; }, prepareWindow: () => {}, cancelled: () => stopping,
  });
  stopping = true;
  finish();
  await assert.rejects(opened, /cancelled/);
  assert.equal(created, 0);
});

test("quit during UI loading destroys the window without showing it", async () => {
  let finish;
  let loaded;
  const loading = new Promise((resolve) => { loaded = resolve; });
  let stopping = false;
  let destroyed = false;
  let shown = 0;
  const window = {
    isDestroyed: () => destroyed,
    destroy: () => { destroyed = true; },
    show: () => { shown++; },
    loadURL: () => new Promise((resolve) => { finish = resolve; loaded(); }),
  };
  const opened = openProductUi({ connection, lifecycle: async () => {}, createWindow: () => window,
    prepareWindow: () => {}, cancelled: () => stopping });
  await loading;
  stopping = true;
  finish();
  await assert.rejects(opened, /cancelled/);
  assert.equal(destroyed, true);
  assert.equal(shown, 0);
});

test("failed UI load destroys its hidden native window", async () => {
  let destroyed = false;
  const window = {
    isDestroyed: () => destroyed, destroy: () => { destroyed = true; },
    show: () => assert.fail("A failed window must stay hidden"),
    loadURL: async () => { throw new Error("Load failed"); },
  };
  await assert.rejects(openProductUi({ connection, lifecycle: async () => {}, createWindow: () => window,
    prepareWindow: () => {}, cancelled: () => false }), /Load failed/);
  assert.equal(destroyed, true);
});
