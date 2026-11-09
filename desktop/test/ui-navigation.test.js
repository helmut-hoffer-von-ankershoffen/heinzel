import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { externalActionUrl } from "../src/external-links.js";
import {
  authenticatedUiUrl, classifyUiLink, guardUiNavigation, handleExternalActions, isOwnedUiNavigation, openProductUi,
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

const APP = [`${connection.origin}/`, `${connection.origin}/settings?x=1`, `${connection.origin}/#help`];
const EXTERNAL = [
  ["https://heinzel.team/docs", "https://heinzel.team/docs"],
  ["http://example.com/a?b=c#d", "http://example.com/a?b=c#d"],
  ["HTTPS://Example.COM/Learn", "https://example.com/Learn"],
  ["https://box.tail1234.ts.net/admin", "https://box.tail1234.ts.net/admin"],
  ["https://8.8.8.8/", "https://8.8.8.8/"],
  ["https://[2606:4700::1111]/", "https://[2606:4700::1111]/"],
  ["mailto:helmut@heinzel.team", "mailto:helmut@heinzel.team"],
  ["mailto:helmut@heinzel.team?subject=Hello%20there", "mailto:helmut@heinzel.team?subject=Hello%20there"],
  ["mailto:a@example.com,b@example.com?cc=c@example.com&subject=Hi&body=Text",
    "mailto:a@example.com,b@example.com?cc=c%40example.com&subject=Hi&body=Text"],
  ["mailto:a@example.com?subject=Hi&attach=/etc/passwd&x-mailer=1", "mailto:a@example.com?subject=Hi"],
];
const DENIED = [
  // schemes
  "javascript:alert(1)", "JaVaScRiPt:alert(1)", " javascript:alert(1)", "data:text/html,x", "file:///etc/passwd",
  "blob:https://a.example/x", "vbscript:x", "ftp://example.com/", "x-apple.systempreferences:com.apple.preference",
  "ms-settings:", "smb://host/share", "about:blank", "chrome://settings", "devtools://devtools", "ws://example.com/",
  // credentials and garbage
  "https://user:pw@example.com/", "https://user@example.com/", "not a URL", "", "/relative", "https://exa\nmple.com/",
  `https://example.com/${"a".repeat(9000)}`, undefined, null, 42,
  // loopback and local names
  "http://127.0.0.1:30124/", "http://127.255.255.254/", "http://127.1/", "http://0x7f000001/", "http://2130706433/",
  "http://0177.0.0.1/", "http://localhost:8080/", "http://localhost./", "http://LOCALHOST../", "http://app.localhost/",
  "http://app.localhost./", "http://printer.local/", "http://db.internal/", "http://nas.lan/", "http://router.home.arpa/",
  "http://intranet/", "http://intranet./",
  // IPv4 ranges
  "http://0.0.0.0/", "http://0.1.2.3/", "http://10.0.0.1/", "http://172.16.0.1/", "http://172.31.255.255/",
  "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data/", "http://100.64.0.1/", "http://100.101.102.103/",
  "http://100.127.255.255/", "http://224.0.0.1/", "http://255.255.255.255/",
  // IPv6
  "http://[::1]:30123/", "http://[::]/", "http://[0:0:0:0:0:0:0:1]/", "http://[::ffff:127.0.0.1]/", "http://[::ffff:7f00:1]/",
  "http://[::ffff:10.0.0.1]/", "http://[::127.0.0.1]/", "http://[fe80::1]/", "http://[febf::1]/", "http://[fc00::1]/",
  "http://[fd7a:115c:a1e0::1]/", "http://[ff02::1]/", "http://[64:ff9b::7f00:1]/", "http://[2002:7f00:1::]/",
  "http://[2001::1]/", "http://[2001:db8::1]/",
  // mailto
  "mailto:", "mailto:?subject=x", "mailto:not-an-address", "mailto:a@example.com?body=line%0Abreak",
  "mailto:a@example.com?subject=%00", "mailto:a@example.com?subject=a&subject=b", "mailto:a@example.com?cc=evil",
  "mailto:a@example.com?subject=x?y", `mailto:a@example.com?body=${"a".repeat(2001)}`, "mailto:a%0A@example.com",
];

test("link classifier: app routes stay, public http(s) and plain mailto go out normalised", () => {
  for (const url of APP) assert.deepEqual(classifyUiLink(url, connection), { kind: "app" }, url);
  for (const [input, href] of EXTERNAL)
    assert.deepEqual(classifyUiLink(input, connection), { kind: "external", url: href }, input);
});

test("link classifier denies every scheme, credential, local host and unsafe mailto case", () => {
  for (const url of DENIED) assert.equal(classifyUiLink(url, connection).kind, "deny", String(url).slice(0, 60));
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
  const logs = [];
  let current = connection;
  let rejectHandoff = false;
  let clock = 0;
  guardUiNavigation(contents, connection);
  handleExternalActions(contents, () => current, async (url) => {
    if (rejectHandoff) throw new Error("System browser unavailable for " + url);
    opened.push(url);
  }, { now: () => clock, log: (fields) => logs.push(fields) });
  const url = "https://meet.google.com/abc-defg-hij";
  for (const disposition of ["foreground-tab", "background-tab", "new-window"]) {
    assert.deepEqual(popup({ url, disposition }), { action: "deny" });
    assert.deepEqual(popup({ url, disposition, postBody: {}, referrer: { url: "https://other.example/" } }), { action: "deny" });
  }
  assert.deepEqual(popup(undefined), { action: "deny" });
  for (const frame of [null, { ...main, frameToken: "child" }, { ...main, processId: 2 },
    { ...main, detached: true }, { ...main, isDestroyed: () => true }, { ...main, url: "https://other.example/" }]) {
    assert.equal(await invoke({ sender: contents, senderFrame: frame }, "join-meeting", url), false);
    assert.equal(await invoke({ sender: contents, senderFrame: frame }, null, url), false);
  }
  const event = { sender: contents, senderFrame: main };
  assert.equal(await invoke({ sender: {}, senderFrame: main }, "join-meeting", url), false);
  assert.equal(await invoke(event, "watch-video", url), false);
  assert.equal(await invoke(event, "", url), false);
  for (const target of [...APP, ...DENIED]) assert.equal(await invoke(event, null, target), false);
  assert.deepEqual(opened, []);
  assert.equal(await invoke(event, "join-meeting", url), true);
  for (let i = 0; i < 50; i++) {
    clock += 10;
    assert.equal(await invoke(event, null, `https://example.com/${i}`), false);
  }
  clock = 2000;
  assert.equal(await invoke(event, null, "https://example.com/x y"), true);
  assert.equal(await invoke(event, "join-meeting", url), false);
  clock = 4000;
  assert.equal(await invoke(event, null, "mailto:a@example.com?subject=hi"), true);
  clock = 6000;
  rejectHandoff = true;
  assert.equal(await invoke(event, "join-meeting", url), false);
  current = null;
  clock = 8000;
  assert.equal(await invoke(event, "join-meeting", url), false);
  assert.equal(await invoke(event, null, url), false);
  assert.deepEqual(opened, [url, "https://example.com/x%20y", "mailto:a@example.com?subject=hi"]);
  assert.deepEqual(logs, [
    { event: "desktop.external_link.throttled", interval_ms: 2000 },
    { event: "desktop.external_link.throttled", interval_ms: 2000 },
    { event: "desktop.external_link.failed" },
  ]);
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
