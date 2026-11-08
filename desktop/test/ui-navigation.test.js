import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { externalActionUrl } from "../src/external-links.js";
import { authenticatedUiUrl, guardUiNavigation, handleExternalActions, isOwnedUiNavigation, openProductUi } from "../src/ui-navigation.js";

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

test("external actions require their own provider routes and return canonical HTTPS URLs", () => {
  const allowed = {
    "join-meeting": [
      "https://zoom.us/j/12345678901", "https://acme.zoom.us/j/12345678901", "https://zoom.com/j/12345678901",
      "https://meet.google.com/abc-defg-hij", "https://teams.microsoft.com/l/meetup-join/x",
      "https://teams.live.com/meet/123", "https://acme.webex.com/meet/a", "https://webex.com/meet/a",
    ],
    "open-calendar": [
      "https://www.google.com/calendar/event?eid=abc", "https://calendar.google.com/calendar/event?eid=abc",
      "https://calendar.google.com/calendar/u/0/r/eventedit/ZmFrZQ",
    ],
    "watch-video": ["https://www.youtube.com/watch?v=tRsQsTMvPNg"],
  };
  for (const [action, targets] of Object.entries(allowed)) {
    for (const target of targets) {
      assert.equal(externalActionUrl(action, target), target);
      for (const other of Object.keys(allowed).filter((key) => key !== action))
        assert.equal(externalActionUrl(other, target), null);
    }
  }
  for (const target of [
    "http://zoom.us/j/1", "https://evil.example/", "https://zoom.us.evil.example/j/1", "https://evilzoom.us/j/1",
    "https://user:pass@zoom.us/j/1", "https://zoom.us:8443/j/1", "https://www.google.com/search?q=1",
    "https://meet.google.com/unrelated", "https://zoom.us/signin", "https://webex.com/redirect",
    "zoommtg://zoom.us/join", "javascript:alert(1)", "file:///etc/passwd", "not a URL", "http://127.0.0.1:30123/",
  ]) assert.equal(externalActionUrl("join-meeting", target), null);
  assert.equal(externalActionUrl("open-calendar", "https://www.google.com/calendar/unrelated"), null);
  assert.equal(externalActionUrl("open-calendar", "https://outlook.office.com/calendar/item/example"), null);
  assert.equal(externalActionUrl("watch-video", "https://www.youtube.com/redirect?v=tRsQsTMvPNg"), null);
  assert.equal(externalActionUrl("watch-video", "https://www.youtube.com/watch?v=tRsQsTMvPNg&v=another"), null);
  assert.equal(externalActionUrl("watch-video", "https://WWW.YOUTUBE.COM:443/watch?v=tRsQsTMvPNg&other=1#example"),
    "https://www.youtube.com/watch?v=tRsQsTMvPNg");
});

test("popup requests have no opener side effect and IPC denies foreign, subframe and stale senders", async () => {
  const main = { url: `${connection.origin}/`, processId: 1, frameToken: "main", isDestroyed: () => false };
  let popup, invoke;
  const contents = Object.assign(new EventEmitter(), {
    mainFrame: main,
    isDestroyed: () => false,
    setWindowOpenHandler: (handler) => { popup = handler; },
    ipc: { handle: (channel, handler) => { assert.equal(channel, "hz:open-external"); invoke = handler; } },
  });
  const opened = [];
  let current = connection;
  guardUiNavigation(contents, connection);
  handleExternalActions(contents, () => current, async (url) => opened.push(url));
  const url = "https://meet.google.com/abc-defg-hij";
  assert.deepEqual(popup({ url, postBody: {}, referrer: { url: "https://other.example/" } }), { action: "deny" });
  assert.deepEqual(popup({ url }), { action: "deny" });
  for (const frame of [null, { ...main, frameToken: "child" }, { ...main, processId: 2 },
    { ...main, detached: true }, { ...main, isDestroyed: () => true }, { ...main, url: "https://other.example/" }])
    assert.equal(await invoke({ sender: contents, senderFrame: frame }, "join-meeting", url), false);
  assert.equal(await invoke({ sender: {}, senderFrame: main }, "join-meeting", url), false);
  assert.equal(await invoke({ sender: contents, senderFrame: main }, "watch-video", url), false);
  assert.deepEqual(opened, []);
  assert.equal(await invoke({ sender: contents, senderFrame: main }, "join-meeting", url), true);
  current = null;
  assert.equal(await invoke({ sender: contents, senderFrame: main }, "join-meeting", url), false);
  assert.deepEqual(opened, [url]);
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
