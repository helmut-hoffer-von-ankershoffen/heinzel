import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import {
  authenticatedUiUrl, classifyUiLink, guardUiNavigation, isOwnedUiNavigation, openProductUi, openUiLinkExternally,
} from "../src/ui-navigation.js";

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

test("link classifier keeps app routes inside, sends public http(s) and mailto out, denies the rest", () => {
  for (const path of ["/", "/settings?x=1", "/#help"])
    assert.deepEqual(classifyUiLink(`${connection.origin}${path}`, connection), { kind: "app" });
  for (const [input, href] of [
    ["https://heinzel.team/docs", "https://heinzel.team/docs"],
    ["http://example.com/a?b=c#d", "http://example.com/a?b=c#d"],
    ["HTTPS://Example.COM/Learn", "https://example.com/Learn"],
    ["mailto:helmut@heinzel.team", "mailto:helmut@heinzel.team"],
  ]) assert.deepEqual(classifyUiLink(input, connection), { kind: "external", url: href });
  for (const url of [
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,x", "file:///etc/passwd", "blob:https://a.example/x",
    "vbscript:x", "ftp://example.com/", "x-apple.systempreferences:com.apple.preference", "ms-settings:", "smb://host/share",
    "about:blank", "chrome://settings", "devtools://devtools", "mailto:", "https://user:pw@example.com/",
    "https://user@example.com/", "http://127.0.0.1:30124/", "http://localhost:8080/", "http://app.localhost/",
    "http://[::1]:30123/", "http://0.0.0.0/", "http://127.1/", "http://0x7f000001/", "not a URL", "", "/relative",
    `https://example.com/${"a".repeat(9000)}`, undefined, null, 42,
  ]) assert.equal(classifyUiLink(url, connection).kind, "deny", String(url).slice(0, 60));
});

test("external opening passes only the parsed href to openExternal and swallows its failure", async () => {
  const opened = [];
  assert.equal(openUiLinkExternally("https://example.com/x y", connection, (url) => { opened.push(url); }), true);
  assert.equal(openUiLinkExternally("javascript:alert(1)", connection, (url) => { opened.push(url); }), false);
  assert.equal(openUiLinkExternally(`${connection.origin}/`, connection, (url) => { opened.push(url); }), false);
  assert.equal(openUiLinkExternally("https://example.com/", connection, async () => { throw new Error("no browser"); }), true);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(opened, ["https://example.com/x%20y"]);
});

test("target=_blank, window.open and middle-click open public links in the browser and deny the window", async () => {
  const contents = new EventEmitter();
  let open;
  contents.setWindowOpenHandler = (handler) => { open = handler; };
  const opened = [];
  guardUiNavigation(contents, connection, (url) => { opened.push(url); });
  for (const disposition of ["foreground-tab", "new-window", "background-tab"])
    assert.deepEqual(open({ url: "https://heinzel.team/learn", disposition, frameName: "_blank" }), { action: "deny" });
  for (const url of ["javascript:alert(1)", "file:///etc/passwd", "http://127.0.0.1:30124/", `${connection.origin}/x`])
    assert.deepEqual(open({ url, disposition: "foreground-tab" }), { action: "deny" });
  assert.deepEqual(open(undefined), { action: "deny" });
  let blocked = 0;
  const event = (url) => ({ url, isMainFrame: true, preventDefault: () => { blocked++; } });
  contents.emit("will-navigate", event(`${connection.origin}/heinzel`));
  contents.emit("will-navigate", event("https://example.com/same-window"));
  contents.emit("will-navigate", event("data:text/html,x"));
  assert.equal(blocked, 2);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(opened, [
    "https://heinzel.team/learn", "https://heinzel.team/learn", "https://heinzel.team/learn",
    "https://example.com/same-window",
  ]);
});

test("the actual navigation handlers block cross-origin main-frame redirects and popups", () => {
  const contents = new EventEmitter();
  let open;
  contents.setWindowOpenHandler = (handler) => { open = handler; };
  guardUiNavigation(contents, connection, () => {});
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
