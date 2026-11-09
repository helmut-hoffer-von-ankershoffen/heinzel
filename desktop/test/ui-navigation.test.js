import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import {
  authenticatedUiUrl, classifyUiLink, createExternalOpener, guardUiNavigation, isOwnedUiNavigation, openProductUi, openUiLinkExternally,
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

test("external opening passes only the normalised href to openExternal and logs its failure", async () => {
  const opened = [];
  assert.equal(openUiLinkExternally("https://example.com/x y", connection, (url) => { opened.push(url); }), true);
  assert.equal(openUiLinkExternally("javascript:alert(1)", connection, (url) => { opened.push(url); }), false);
  assert.equal(openUiLinkExternally(`${connection.origin}/`, connection, (url) => { opened.push(url); }), false);
  const logs = [];
  const failing = createExternalOpener({ connection, openExternal: async () => { throw new Error("no browser"); },
    log: (fields) => logs.push(fields) });
  assert.equal(failing("https://example.com/"), true);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(opened, ["https://example.com/x%20y"]);
  assert.deepEqual(logs, [{ event: "desktop.external_link.failed" }]);
});

test("external opens are rate limited per window: one per 2 s, the rest dropped and logged once", async () => {
  let clock = 1_000_000;
  const opened = [];
  const logs = [];
  const open = createExternalOpener({ connection, openExternal: (url) => { opened.push(url); },
    now: () => clock, log: (fields) => logs.push(fields) });
  assert.equal(open("https://a.example.com/"), true);
  for (let i = 0; i < 50; i++) { clock += 10; assert.equal(open(`https://spam.example.com/${i}`), false); }
  assert.equal(open("javascript:alert(1)"), false);
  clock += 2000;
  assert.equal(open("https://b.example.com/"), true);
  clock += 1;
  assert.equal(open("https://c.example.com/"), false);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(opened, ["https://a.example.com/", "https://b.example.com/"]);
  assert.deepEqual(logs, [
    { event: "desktop.external_link.throttled", interval_ms: 2000 },
    { event: "desktop.external_link.throttled", interval_ms: 2000 },
  ]);
});

test("target=_blank, window.open and middle-click from any frame go to the browser; the window is denied", async () => {
  const contents = new EventEmitter();
  let open;
  contents.setWindowOpenHandler = (handler) => { open = handler; };
  const opened = [];
  let clock = 0;
  guardUiNavigation(contents, connection, (url) => { opened.push(url); }, { now: () => clock, log: () => {} });
  for (const disposition of ["foreground-tab", "new-window", "background-tab"]) {
    clock += 5000;
    assert.deepEqual(open({ url: "https://heinzel.team/learn", disposition, frameName: "_blank" }), { action: "deny" });
  }
  for (const url of ["javascript:alert(1)", "file:///etc/passwd", "http://127.0.0.1:30124/", `${connection.origin}/x`]) {
    clock += 5000;
    assert.deepEqual(open({ url, disposition: "foreground-tab" }), { action: "deny" });
  }
  assert.deepEqual(open(undefined), { action: "deny" });
  clock += 5000;
  for (let i = 0; i < 20; i++) assert.deepEqual(open({ url: `https://embed.example.com/${i}` }), { action: "deny" });
  let blocked = 0;
  const event = (url) => ({ url, isMainFrame: true, preventDefault: () => { blocked++; } });
  clock += 5000;
  contents.emit("will-navigate", event(`${connection.origin}/heinzel`));
  contents.emit("will-navigate", event("https://example.com/same-window"));
  contents.emit("will-navigate", event("data:text/html,x"));
  assert.equal(blocked, 2);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(opened, [
    "https://heinzel.team/learn", "https://heinzel.team/learn", "https://heinzel.team/learn",
    "https://embed.example.com/0", "https://example.com/same-window",
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
