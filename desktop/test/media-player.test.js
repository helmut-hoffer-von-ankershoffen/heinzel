import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createMediaPlayer, PLAYER_ORIGIN, playerScript, playerState, playerUrl } from "../src/media-player.js";
import { BROWSER_PARTITION, createMiniBrowser } from "../src/mini-browser.js";

const ORIGIN = "http://127.0.0.1:8737";
const VIDEO = "abcdefghijk";

/** A product window, a fake website partition with a cookie jar, and both controllers on it. */
function fixture({ answer = () => ({ ready: true, state: 1, error: false }), platform = "darwin" } = {}) {
  const frame = { processId: 1, frameToken: "main", url: `${ORIGIN}/`, detached: false, isDestroyed: () => false };
  const sender = Object.assign(new EventEmitter(), { mainFrame: frame, isDestroyed: () => false, getZoomFactor: () => 1 });
  const calls = [], views = [], sessions = [], opened = [];
  const owner = Object.assign(new EventEmitter(), { webContents: sender, isDestroyed: () => false, isFocused: () => true,
    getContentSize: () => [1000, 700], contentView: { addChildView: () => calls.push(["add"]), removeChildView: () => calls.push(["remove"]) } });
  const createSession = (partition) => {
    const existing = sessions.find((session) => session.partition === partition);
    if (existing) return existing;
    const session = Object.assign(new EventEmitter(), { partition, cookies: new Map(), clearStorageData: async () => session.cookies.clear(),
      setPermissionCheckHandler: () => {}, setPermissionRequestHandler: () => {}, setDevicePermissionHandler: () => {},
      setBluetoothPairingHandler: () => {}, setDisplayMediaRequestHandler: () => {},
      webRequest: { onBeforeRequest: (handler) => { session.before = handler; } } });
    sessions.push(session);
    return session;
  };
  const createView = (preferences) => {
    let url = "", dead = false;
    const contents = Object.assign(new EventEmitter(), { preferences, isDestroyed: () => dead, mainFrame: { top: null },
      close: () => { dead = true; calls.push(["close"]); }, getURL: () => url,
      loadURL: async (target, options) => { url = target; calls.push(["load", target, options]); },
      reload: () => {}, navigationHistory: { canGoBack: () => false, canGoForward: () => false },
      setWebRTCIPHandlingPolicy: () => {}, setWindowOpenHandler: (handler) => { contents.popup = handler; },
      executeJavaScript: async (code, gesture) => { calls.push(["script", code, gesture]); return code.includes("getPlayerState") ? answer() : true; } });
    const view = { webContents: contents, setBounds: (bounds) => calls.push(["bounds", bounds]), setVisible: (visible) => calls.push(["visible", visible]) };
    views.push(view);
    return view;
  };
  const common = { windows: new WeakSet([owner]), fromWebContents: (contents) => (contents === sender ? owner : null),
    connection: () => ({ origin: ORIGIN, token: "a".repeat(32) }) };
  const network = { configure: async () => calls.push(["network-ready"]), close: () => calls.push(["network-close"]),
    authenticate: (_info, callback) => callback("guest", "secret") };
  const browser = createMiniBrowser({ ...common, platform, createSession, createView, createNetwork: async () => network });
  const player = createMediaPlayer({ ...common, platform, createView, lease: (page) => browser.lease(page),
    openExternal: async (url) => { opened.push(url); } });
  const as = (controller) => (request) => controller.handle({ sender, senderFrame: frame }, request);
  return { owner, sender, calls, views, sessions, opened, browser, player, play: as(player), browse: as(browser) };
}

test("the player guest is the YouTube embed on the shared website partition, never the product session", async () => {
  const f = fixture();
  const state = await f.play({ action: "open", video: VIDEO });
  assert.deepEqual(state, { status: "loading", ready: true, state: 1, error: false });
  const [view] = f.views;
  const preferences = view.webContents.preferences.webPreferences;
  assert.equal(preferences.session.partition, BROWSER_PARTITION);
  assert.equal(preferences.preload, undefined);
  for (const key of ["nodeIntegration", "nodeIntegrationInSubFrames", "webviewTag"]) assert.equal(preferences[key], false);
  assert.equal(preferences.sandbox, true);
  assert.equal(preferences.contextIsolation, true);
  const load = f.calls.find(([name]) => name === "load");
  assert.equal(load[1], `${PLAYER_ORIGIN}/embed/${VIDEO}?playsinline=1&rel=0`);
  assert.deepEqual(load[2], { httpReferrer: `${ORIGIN}/` });
  // The local service stays unreachable from the partition the player shares.
  const session = f.sessions[0];
  for (const url of [`${ORIGIN}/api/system/config/show`, "http://localhost:8737/", "file:///etc/hosts"])
    session.before({ url }, (result) => assert.equal(result.cancel, true, url));
  session.before({ url: "https://www.youtube.com/embed/abcdefghijk" }, (result) => assert.equal(result.cancel, false));
});

test("a sign-in cookie set in the sidebar browser is visible to the player guest, and Clear removes it for both", async () => {
  const f = fixture();
  await f.browse({ action: "navigate", url: "https://accounts.example.com/" });
  const browserSession = f.views[0].webContents.preferences.webPreferences.session;
  browserSession.cookies.set("SID", "signed-in");
  await f.play({ action: "open", video: VIDEO });
  const playerSession = f.views[1].webContents.preferences.webPreferences.session;
  assert.equal(playerSession, browserSession);
  assert.equal(playerSession.cookies.get("SID"), "signed-in");
  assert.equal(f.sessions.length, 1);
  // Clear takes the playing embed off YouTube before the wipe, then reloads it with its referrer.
  const player = f.views[1].webContents;
  const order = [];
  player.loadURL = async (target, options) => { order.push(["player", target, options]); };
  const wipe = playerSession.clearStorageData;
  playerSession.clearStorageData = async () => { order.push(["wipe"]); await wipe(); };
  await f.browse({ action: "clear" });
  assert.deepEqual(order, [["player", "about:blank", undefined], ["wipe"],
    ["player", `${PLAYER_ORIGIN}/embed/${VIDEO}?playsinline=1&rel=0`, { httpReferrer: `${ORIGIN}/` }]]);
  assert.equal(playerSession.cookies.size, 0);
});

test("a failed open releases its lease, also when the product connection is gone", async () => {
  const f = fixture();
  let product = { origin: ORIGIN, token: "a".repeat(32) };
  const player = createMediaPlayer({ windows: new WeakSet([f.owner]), fromWebContents: (contents) => (contents === f.sender ? f.owner : null),
    connection: () => product, lease: () => f.browser.lease(), createView: () => { throw new Error("no view"); }, openExternal: async () => {} });
  const handle = (request) => player.handle({ sender: f.sender, senderFrame: f.sender.mainFrame }, request);
  await assert.rejects(handle({ action: "open", video: VIDEO }), /no view/);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(f.calls.filter(([name]) => name === "network-ready").length, 1);
  assert.equal(f.calls.filter(([name]) => name === "network-close").length, 1);
  product = null;
  await assert.rejects(handle({ action: "open", video: VIDEO }));
});

test("one broker serves both guests and ends only after the last of them closes", async () => {
  const f = fixture();
  await f.browse({ action: "navigate", url: "https://example.com/" });
  await f.play({ action: "open", video: VIDEO });
  assert.equal(f.calls.filter(([name]) => name === "network-ready").length, 1);
  await f.browse({ action: "close" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(f.calls.filter(([name]) => name === "network-close").length, 0);
  await f.play({ action: "close" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(f.calls.filter(([name]) => name === "network-close").length, 1);
});

test("the bridge carries only play, pause, volume and state, as fixed scripts without a gesture", async () => {
  const f = fixture();
  await f.play({ action: "open", video: VIDEO });
  await f.play({ action: "play" });
  await f.play({ action: "volume", value: 35 });
  await f.play({ action: "pause" });
  const scripts = f.calls.filter(([name]) => name === "script").map(([, code, gesture]) => [code, gesture]);
  // No bridge call ever hands the page a user activation.
  assert.ok(scripts.every(([, gesture]) => gesture === false));
  const commands = scripts.map(([code]) => code).filter((code) => !code.includes("getPlayerState"));
  assert.deepEqual(commands, [playerScript("play"), playerScript("volume", 35), playerScript("pause")]);
  assert.match(commands[1], /p\.setVolume\(35\)/);
  for (const request of [
    { action: "volume", value: 101 }, { action: "volume", value: -1 }, { action: "volume", value: 1.5 },
    { action: "volume", value: "1); alert(1" }, { action: "seek", value: 3 }, { action: "eval", code: "1" },
    { action: "play", extra: true }, { action: "open", video: "../../x" }, { action: "open", video: VIDEO, url: "https://evil.example/" },
    null,
  ]) await assert.rejects(f.play(request), undefined, JSON.stringify(request));
  // A guest or another frame can never drive it.
  await assert.rejects(f.player.handle({ sender: { isDestroyed: () => false }, senderFrame: {} }, { action: "play" }));
});

test("the page's state answer is reduced to the known shape", () => {
  assert.deepEqual(playerState({ ready: true, state: 1, error: false }), { ready: true, state: 1, error: false });
  assert.deepEqual(playerState({ ready: "yes", state: 99, error: 1, extra: "x" }), { ready: false, state: -1, error: false });
  assert.deepEqual(playerState(null), { ready: false, state: -1, error: false });
  assert.deepEqual(playerState({ ready: true, state: 2, error: true }), { ready: true, state: 2, error: true });
});

test("embed links open in the default browser only after input in the player; its own page may not change", async () => {
  let clock = 10_000;
  const f = fixture();
  await f.play({ action: "open", video: VIDEO });
  const contents = f.views[0].webContents;
  assert.deepEqual(contents.popup({ url: "https://support.google.com/youtube/answer/1" }), { action: "deny" });
  contents.emit("before-mouse-event", {}, { type: "mouseDown" });
  assert.deepEqual(contents.popup({ url: "https://accounts.google.com/ServiceLogin" }), { action: "deny" });
  let blocked = 0;
  const navigate = (url) => contents.emit("will-navigate", { url, preventDefault: () => { blocked++; } });
  navigate(`${PLAYER_ORIGIN}/embed/${VIDEO}?autoplay=1`);
  assert.equal(blocked, 0);
  navigate(`${PLAYER_ORIGIN}/watch?v=${VIDEO}`);
  navigate("javascript:alert(1)");
  assert.equal(blocked, 2);
  contents.emit("before-mouse-event", {}, { type: "mouseDown" });
  contents.popup({ url: "file:///etc/passwd" });
  let redirected = 0;
  const redirect = (url, isMainFrame = true) => contents.emit("will-redirect", { url, isMainFrame, preventDefault: () => { redirected++; } });
  // The main frame may only reach the embed itself or YouTube's EU consent page.
  redirect("https://consent.youtube.com/m?continue=x");
  redirect(`${PLAYER_ORIGIN}/embed/${VIDEO}?x=1`);
  assert.equal(redirected, 0);
  redirect("https://ads.example.com/landing");
  redirect(`${PLAYER_ORIGIN}/watch?v=${VIDEO}`);
  redirect("http://127.0.0.1:8737/");
  assert.equal(redirected, 3);
  redirect("https://googleads.example.com/frame", false);
  redirect("http://127.0.0.1:8737/", false);
  contents.emit("will-frame-navigate", { url: "https://192.168.1.2/", isMainFrame: false, preventDefault: () => { redirected++; } });
  assert.equal(redirected, 5);
  navigate("https://consent.youtube.com/save");
  assert.equal(blocked, 2);
  // Electron would choose the first Bluetooth device on its own: the player refuses.
  let bluetooth = "unset", prevented = false;
  contents.emit("select-bluetooth-device", { preventDefault: () => { prevented = true; } }, [{ deviceId: "b1" }], (id) => { bluetooth = id; });
  assert.equal(prevented, true);
  assert.equal(bluetooth, "");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(f.opened, ["https://accounts.google.com/ServiceLogin"]);
  clock += 1;
});

test("layout, hide, product navigation and window close keep the player inside its owner", async () => {
  const f = fixture();
  assert.deepEqual(await f.play({ action: "state" }), { status: "empty", ready: false, state: -1, error: false });
  await f.play({ action: "open", video: VIDEO });
  await f.play({ action: "layout", bounds: { x: 10, y: 10, width: 320, height: 180 } });
  assert.deepEqual(f.calls.at(-2), ["visible", true]);
  await assert.rejects(f.play({ action: "layout", bounds: { x: 900, y: 10, width: 320, height: 180 } }));
  await f.play({ action: "hide" });
  assert.ok(f.calls.some(([name, visible]) => name === "visible" && visible === false));
  // The same video keeps its guest; another video replaces it.
  await f.play({ action: "open", video: VIDEO });
  assert.equal(f.views.length, 1);
  await f.play({ action: "open", video: "zyxwvutsrqp" });
  assert.equal(f.views.length, 2);
  assert.equal(f.views[0].webContents.isDestroyed(), true);
  f.sender.emit("did-start-navigation", { isMainFrame: true, isSameDocument: false });
  assert.equal(f.views[1].webContents.isDestroyed(), true);
  await f.play({ action: "open", video: VIDEO });
  f.owner.emit("closed");
  assert.equal(f.views[2].webContents.isDestroyed(), true);
  f.player.closeAll();
  await assert.rejects(f.play({ action: "open", video: VIDEO }));
});

test("the embed address takes only an 11-character video id", () => {
  assert.equal(playerUrl(VIDEO), "https://www.youtube.com/embed/abcdefghijk?playsinline=1&rel=0");
  for (const video of ["", "short", "abcdefghijk?x", "abcdefghij/", "../abcdefgh", 12345678901, null])
    assert.throws(() => playerUrl(video));
  assert.throws(() => playerScript("eval"));
});

test("a page that never answers a bridge call costs at most the timeout", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const f = fixture();
  await f.play({ action: "open", video: VIDEO });
  f.views[0].webContents.executeJavaScript = () => new Promise(() => {});
  const pending = f.play({ action: "pause" });
  for (let i = 0; i < 2; i++) { await Promise.resolve(); t.mock.timers.tick(2000); await new Promise((resolve) => setImmediate(resolve)); }
  assert.deepEqual(await pending, { status: "loading", ready: false, state: -1, error: false });
});

test("a player still opening when browsing data is cleared is restored after the wipe", async () => {
  const f = fixture();
  await f.browse({ action: "clear" });
  const session = f.sessions[0];
  session.clearStorageData = async () => { f.calls.push(["wipe"]); };
  const opening = f.play({ action: "open", video: VIDEO });
  const clearing = f.browse({ action: "clear" });
  await Promise.all([opening, clearing]);
  const embed = `${PLAYER_ORIGIN}/embed/${VIDEO}?playsinline=1&rel=0`;
  const lastLoad = f.calls.findLastIndex(([name, target]) => name === "load" && target === embed);
  // Whatever order the two took, the embed loads again after the wipe.
  assert.ok(lastLoad > f.calls.findIndex(([name]) => name === "wipe"), f.calls.map(([name, target]) => target ?? name).join(" "));
});

test("main hands the player's page to the browser's lease, so Clear browsing data reaches the player", async () => {
  const main = await readFile(new URL("../src/main.js", import.meta.url), "utf8");
  const wiring = /const mediaPlayer = createMediaPlayer\(\{([\s\S]*?)\n\}\);/.exec(main)?.[1] ?? "";
  assert.match(wiring, /\blease: \((\w+)\) => miniBrowser\.lease\(\1\),/, "the lease must forward the page it is given");
});

/** As Electron does once a window is destroyed: it says so, and everything else of it throws. */
function destroyWindow(owner) {
  const gone = () => { throw new TypeError("Object has been destroyed"); };
  owner.isDestroyed = () => true;
  for (const key of ["webContents", "contentView"]) Object.defineProperty(owner, key, { get: gone });
  owner.isFocused = gone;
  owner.getContentSize = gone;
}

test("quit after the window is gone: the player closes, gives its lease back and leaves no listener", async () => {
  const f = fixture();
  await f.play({ action: "open", video: VIDEO });
  assert.equal(f.sender.listenerCount("did-start-navigation"), 1);
  destroyWindow(f.owner);
  assert.doesNotThrow(() => f.player.closeAll());
  assert.equal(f.views[0].webContents.isDestroyed(), true);
  for (const name of ["closed", "blur"]) assert.equal(f.owner.listenerCount(name), 0, name);
  assert.equal(f.sender.listenerCount("did-start-navigation"), 0);
  // The lease is back: with no tab open either, the shared network closes.
  assert.doesNotThrow(() => f.browser.closeAll());
  // The network is let go once its guests are closed, a moment later.
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(f.calls.filter(([name]) => name === "network-close"), [["network-close"]]);
  assert.doesNotThrow(() => f.owner.emit("closed"));
  // A player whose view cannot be closed any more still gives its lease back.
  const g = fixture();
  await g.play({ action: "open", video: VIDEO });
  g.views[0].webContents.close = () => { throw new TypeError("Object has been destroyed"); };
  assert.doesNotThrow(() => g.player.closeAll());
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(g.calls.filter(([name]) => name === "network-close"), [["network-close"]]);
});

test("the player's guest gets spell check only on macOS, where it downloads nothing", async () => {
  for (const [platform, expected] of [["darwin", true], ["linux", false], ["win32", false]]) {
    const f = fixture({ platform });
    await f.play({ action: "open", video: VIDEO });
    assert.equal(f.views[0].webContents.preferences.webPreferences.spellcheck, expected, platform);
  }
});
