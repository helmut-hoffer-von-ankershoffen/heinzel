import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import test from "node:test";
import { appMenuTemplate } from "../src/app-menu.js";
import { createInstanceLifecycle } from "../src/instance-lifecycle.js";
import { createLifecycleHandler } from "../src/lifecycle-ipc.js";
import { createWorkspaceController, isPickerFrame } from "../src/workspace-controller.js";
import { guardUiNavigation, openProductUi } from "../src/ui-navigation.js";
import { stopWithReport } from "../src/shutdown.js";
import { reattach } from "../src/reattach.js";
import { createMiniBrowser } from "../src/mini-browser.js";
import { createMediaPlayer } from "../src/media-player.js";

const moduleUrl = new URL("../src/main.js", import.meta.url).href;
const source = readFileSync(new URL(moduleUrl), "utf8").replace(/^import .*;\n/gm, "").replaceAll("import.meta.url", "moduleUrl");
const local = { origin: "http://127.0.0.1:1234", token: "l".repeat(32) };
const remote = { kind: "link", link: `https://studio.example/#token=${"r".repeat(32)}` };
const flush = () => new Promise(setImmediate);

async function fixture({ startupFails = false, failSecondProbe = false, initialLoadFails = false, deferredStartup = false, deferredLoad = false, reattachFailures = 0 } = {}) {
  const windows = [], sessions = [], handlers = new Map(), events = [];
  let menu, rejected = false, focused;
  let remoteProbes = 0;
  let finishLoad, failLoad, expireLoad;
  const load = new Promise((resolve, reject) => { finishLoad = resolve; failLoad = reject; });
  class Window extends EventEmitter {
    constructor(options) {
      super(); this.options = options; this.destroyed = false; this.visible = false;
      const session = options.webPreferences.session ?? makeSession(options.webPreferences.partition);
      const contents = Object.assign(new EventEmitter(), {
        session, mainFrame: { url: "", isDestroyed: () => this.destroyed, detached: false },
        isDestroyed: () => this.destroyed, setWindowOpenHandler() {}, send() {}, getZoomFactor: () => 1,
      });
      this.webContents = contents; this.contentView = { addChildView() {}, removeChildView() {} }; windows.push(this);
    }
    async loadURL(url) {
      this.webContents.mainFrame.url = url;
      if (deferredLoad && this === windows[0]) await load;
      if (initialLoadFails && this === windows[0]) throw new Error("load failed");
    }
    isDestroyed() { return this.destroyed; }
    isVisible() { return this.visible; }
    isMinimized() { return false; }
    isFocused() { return focused === this; }
    getContentSize() { return [1000, 700]; }
    show() { this.visible = true; focused = this; }
    focus() { focused = this; }
    hide() { this.visible = false; }
    close() { this.destroy(); }
    destroy() { this.destroyed = true; this.webContents.emit("destroyed"); this.emit("closed"); }
    static getAllWindows() { return windows.filter((window) => !window.destroyed); }
    static fromWebContents(contents) { return windows.find((window) => window.webContents === contents); }
    static getFocusedWindow() { return focused; }
  }
  function makeSession(partition) {
    const session = { partition,
      setProxy: async (value) => { session.proxy = value; },
      setPermissionCheckHandler: (value) => { session.permission = value; },
      setPermissionRequestHandler: (value) => { session.requestPermission = value; },
      setDisplayMediaRequestHandler() {},
      setDevicePermissionHandler() {}, setBluetoothPairingHandler() {},
      webRequest: { onBeforeRequest() {} }, on() {},
      clearStorageData: async () => { session.cleared = true; }, clearCache: async () => {},
    };
    sessions.push(session); return session;
  }
  const app = Object.assign(new EventEmitter(), {
    isPackaged: false, name: "Heinzelmännchen", requestSingleInstanceLock: () => true,
    whenReady: () => Promise.resolve(), getPath: () => "/fixture", getVersion: () => "1.0.0", setAboutPanelOptions() {},
    quit: () => events.push("quit"),
  });
  const native = () => ({ revoke() {}, navigation() {}, check: () => true, request() {}, display() {} });
  function guestView() {
    let url = "", dead = false;
    const contents = Object.assign(new EventEmitter(), { mainFrame: { top: null },
      isDestroyed: () => dead, close: () => { dead = true; }, getURL: () => url,
      loadURL: async (value) => { url = value; }, reload() {},
      navigationHistory: { canGoBack: () => false, canGoForward: () => false },
      setWebRTCIPHandlingPolicy() {}, setWindowOpenHandler() {},
      executeJavaScript: async () => ({ ready: true, state: 1, error: false }),
    });
    return { webContents: contents, setBounds() {}, setVisible() {} };
  }
  let serviceCount = 0, running = !startupFails;
  let finishStartup, failStartup;
  const startup = new Promise((resolve, reject) => { finishStartup = resolve; failStartup = reject; });
  const cliOptions = [], losses = [], waits = [];
  let failuresLeft = reattachFailures;
  const cli = (options = {}) => {
    serviceCount++;
    cliOptions.push(options);
    const connection = serviceCount === 1 ? local : { origin: "http://127.0.0.1:4567", token: "n".repeat(32) };
    let lose;
    const exited = new Promise((resolve) => { lose = resolve; });
    losses.push(() => { running = false; lose({ code: null, signal: null, serviceLost: true }); });
    return { exited: () => exited, isRunning: () => running,
      start: async () => {
        events.push("start-local");
        if (deferredStartup && serviceCount === 1) await startup;
        if (startupFails) throw new Error("startup");
        if (serviceCount > 1 && failuresLeft > 0) { failuresLeft--; throw new Error("service still restarting"); }
        running = true; return connection;
      },
      stop: async () => { events.push("stop-local"); running = false; return { outcome: "graceful" }; },
    };
  };
  runInNewContext(source, {
    moduleUrl, URL, path, fileURLToPath, randomUUID: () => String(sessions.length), console,
    process: Object.assign(new EventEmitter(), { argv: [], env: {}, platform: "darwin" }),
    app, BrowserWindow: Window, dialog: { showErrorBox: () => events.push("error-box") },
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    Menu: { buildFromTemplate: (value) => value, setApplicationMenu: (value) => { menu = value; } },
    session: { fromPartition: makeSession }, safeStorage: {}, shell: { openExternal() {} },
    createWorkspaceDirectory: () => ({ list: () => [], connection: () => null, remember: () => true, forget: () => true }),
    createWorkspaceController, isPickerFrame, createInstanceLifecycle, createLifecycleHandler, guardUiNavigation,
    openProductUi: (options) => openProductUi(deferredLoad ? { ...options,
      schedule: (callback) => { if (!expireLoad) { expireLoad = callback; return null; } return setTimeout(callback, 15000); },
      unschedule: (timer) => { if (timer) clearTimeout(timer); },
    } : options),
    lifecycle: async (connection) => {
      events.push(["verify", connection.origin]);
      if (connection.remote) remoteProbes++;
      if (rejected || (failSecondProbe && remoteProbes === 2)) throw Object.assign(new Error("Sign-in required"), { status: 401 });
      return {};
    },
    windowOptions: () => ({ show: false }),
    initializeDesktopIdentity: () => true, createNativePermissions: native, createWorkspaceAudio: native,
    systemAudioAvailable: () => false, release: () => "fixture",
    createMiniBrowser: (options) => createMiniBrowser({ ...options, createView: guestView,
      createNetwork: async () => ({ configure: async () => {}, close() {}, authenticate: (_info, callback) => callback() }) }),
    createMediaPlayer: (options) => createMediaPlayer({ ...options, createView: guestView }),
    createBrowserAgent: () => ({}), createBrowserBridge: () => ({ start: () => events.push("bridge-start"), stop: () => events.push("bridge-stop") }),
    newBridgeToken: () => "bridge-secret", BRIDGE_TOKEN_ENV: "BRIDGE", createOwnedCli: cli,
    installSignalQuit: () => ({}), createAboutHandler: () => () => {}, createAboutLinkHandler: () => () => {},
    createPublicMediaHandler: () => () => {}, appMenuTemplate, menuLogLine: () => "menu", stopWithReport,
    developmentCli: () => "hz",
    reattach: (attempt, options) => reattach(attempt, { ...options, wait: async (ms) => { waits.push(ms); } }),
  });
  await flush(); await flush();
  const openPicker = async () => {
    menu[0].submenu.find((item) => item.label === "Connection…").click(); await flush();
    return Window.getAllWindows().find((window) => window.options.webPreferences.preload.endsWith("workspace-picker-preload.cjs"));
  };
  const request = (picker, value) => handlers.get("hz:workspace")({ sender: picker.webContents, senderFrame: picker.webContents.mainFrame }, value);
  const surface = (window, name, value) => handlers.get(name)({ sender: window.webContents, senderFrame: window.webContents.mainFrame }, value);
  return { windows, sessions, handlers, events, openPicker, request, surface, services: () => serviceCount,
    cliOptions, waits, loseService: () => losses.at(-1)(),
    reject: () => { rejected = true; }, stopLocal: () => { running = false; }, finishStartup, failStartup, finishLoad, failLoad,
    expireLoad: () => expireLoad() };
}

test("main switches local to remote and back with fresh sessions and no remote native authority", async () => {
  const f = await fixture();
  const first = f.windows[0];
  assert.equal(first.webContents.session.proxy.mode, "direct");
  const picker = await f.openPicker();
  assert.equal((await f.request(picker, remote)).message, "Connected");
  const remoteWindow = f.windows.at(-1);
  assert.equal(first.isDestroyed(), true);
  assert.equal(first.webContents.session.cleared, true);
  assert.match(remoteWindow.options.webPreferences.preload, /workspace-remote-preload/);
  assert.equal(remoteWindow.webContents.session.permission(), false);
  assert.equal(remoteWindow.options.webPreferences.nodeIntegration, false);
  assert.equal(remoteWindow.options.webPreferences.sandbox, true);
  assert.equal(picker.isVisible(), false);
  await assert.rejects(f.handlers.get("hz:workspace")({ sender: remoteWindow.webContents, senderFrame: remoteWindow.webContents.mainFrame }, { kind: "local" }));
  await assert.rejects(f.handlers.get("hz:lifecycle")({ sender: remoteWindow.webContents, senderFrame: remoteWindow.webContents.mainFrame }));
  assert.equal(f.services(), 1);
  assert.equal(f.events.includes("stop-local"), false);
  assert.equal(f.events.filter((event) => event === "bridge-start").length, 1);
  await f.openPicker();
  await f.request(picker, { kind: "local" });
  assert.equal(remoteWindow.isDestroyed(), true);
  assert.equal(remoteWindow.webContents.session.cleared, true);
  assert.equal(f.services(), 1);
  assert.equal(f.events.filter((event) => event === "bridge-start").length, 2);
  assert.equal(f.events.includes("quit"), false);
  assert.notEqual(f.windows.at(-1).webContents.session, first.webContents.session);
});

test("main preserves the working window when remote authentication fails", async () => {
  const f = await fixture();
  const first = f.windows[0], picker = await f.openPicker();
  f.reject();
  assert.match((await f.request(picker, remote)).message, /Sign-in required/);
  assert.equal(first.isDestroyed(), false);
  assert.equal(picker.isVisible(), true);
});

test("a failed second probe rolls back to the old renderer and local bridge", async () => {
  const f = await fixture({ failSecondProbe: true });
  const first = f.windows[0], picker = await f.openPicker();
  await f.surface(first, "hz:browser", { action: "navigate", url: "https://example.com/" });
  await f.surface(first, "hz:player", { action: "open", video: "abcdefghijk" });
  const result = await f.request(picker, remote);
  assert.match(result.message, /Sign-in required/);
  assert.equal(result.current, local.origin);
  assert.equal(first.isDestroyed(), false);
  assert.equal(first.webContents.session.cleared, undefined);
  assert.equal(f.events.filter((event) => event === "bridge-start").length, 2);
  assert.equal((await f.surface(first, "hz:player", { action: "state" })).ready, true);
  await f.surface(first, "hz:browser", { action: "navigate", url: "https://example.org/" });
});

test("returning to local restores real Browser and player controllers", async () => {
  const f = await fixture();
  const first = f.windows[0], picker = await f.openPicker();
  await f.surface(first, "hz:browser", { action: "navigate", url: "https://example.com/" });
  await f.surface(first, "hz:player", { action: "open", video: "abcdefghijk" });
  await f.request(picker, remote);
  await f.openPicker();
  await f.request(picker, { kind: "local" });
  const current = f.windows.at(-1);
  await f.surface(current, "hz:browser", { action: "navigate", url: "https://example.org/" });
  assert.equal((await f.surface(current, "hz:player", { action: "open", video: "abcdefghijk" })).ready, true);
});

test("a failed initial renderer load opens recovery without quitting", async () => {
  const f = await fixture({ initialLoadFails: true });
  const picker = f.windows.find((window) => window.options.webPreferences.preload.endsWith("workspace-picker-preload.cjs"));
  assert.ok(picker?.isVisible());
  assert.equal(f.events.includes("quit"), false);
  assert.equal((await f.request(picker, remote)).message, "Connected");
});

test("retry after initial renderer failure reopens the running local service", async () => {
  const f = await fixture({ initialLoadFails: true });
  const picker = f.windows.find((window) => window.options.webPreferences.preload.endsWith("workspace-picker-preload.cjs"));
  const state = await f.request(picker, { kind: "state" });
  assert.equal(state.canRetry, true);
  assert.match(state.message, /Connection restored|out of reach/);
  assert.equal((await f.request(picker, { kind: "retry" })).message, "Connected");
  assert.equal(f.services(), 1);
  assert.equal(f.windows[0].isDestroyed(), true);
  assert.equal(f.windows.at(-1).isVisible(), true);
});

test("an unselected stalled startup opens retryable recovery and ignores its late load", async () => {
  const f = await fixture({ deferredLoad: true });
  const first = f.windows[0];
  f.expireLoad();
  await flush(); await flush();
  const picker = f.windows.find((window) => window.options.webPreferences.preload.endsWith("workspace-picker-preload.cjs"));
  assert.ok(picker?.isVisible());
  assert.equal(first.isDestroyed(), true);
  assert.equal(f.events.includes("quit"), false);
  const state = await f.request(picker, { kind: "state" });
  assert.equal(state.canRetry, true);
  assert.equal(state.problem, false);
  assert.match(state.message, /Connection restored/);
  assert.equal((await f.request(picker, { kind: "retry" })).message, "Connected");
  const recovered = f.windows.at(-1);
  assert.equal(recovered.isVisible(), true);
  assert.equal(f.services(), 1);
  f.finishLoad();
  await flush(); await flush();
  assert.equal(first.isVisible(), false);
  assert.equal(recovered.isDestroyed(), false);
  assert.equal((await f.request(picker, { kind: "state" })).current, local.origin);
});

test("retry after a local renderer crash replaces the failed window", async () => {
  const f = await fixture();
  const first = f.windows[0];
  first.webContents.emit("render-process-gone");
  await flush();
  const picker = f.windows.find((window) => window.options.webPreferences.preload.endsWith("workspace-picker-preload.cjs"));
  assert.ok(picker?.isVisible());
  assert.equal((await f.request(picker, { kind: "retry" })).message, "Connected");
  assert.equal(first.isDestroyed(), true);
  assert.notEqual(f.windows.at(-1), first);
  assert.equal(f.windows.at(-1).isVisible(), true);
});

test("retry starts a stopped local service and opens its fresh credentials", async () => {
  const f = await fixture();
  const first = f.windows[0];
  f.stopLocal();
  first.webContents.emit("render-process-gone");
  await flush();
  const picker = f.windows.find((window) => window.options.webPreferences.preload.endsWith("workspace-picker-preload.cjs"));
  assert.equal((await f.request(picker, { kind: "retry" })).message, "Connected");
  assert.equal(f.services(), 2);
  assert.equal(first.isDestroyed(), true);
  assert.equal(f.windows.at(-1).webContents.mainFrame.url, `http://127.0.0.1:4567/#token=${"n".repeat(32)}`);
});

test("closing the last workspace still quits when the picker is hidden", async () => {
  const f = await fixture();
  const picker = await f.openPicker();
  picker.hide();
  f.windows[0].close();
  assert.equal(f.events.includes("quit"), true);
});

test("a failed local startup leaves a usable picker and remote connection", async () => {
  const f = await fixture({ startupFails: true });
  const picker = await f.openPicker();
  assert.equal(picker.isVisible(), true);
  assert.equal((await f.request(picker, remote)).message, "Connected");
  assert.equal(f.events.includes("quit"), false);
  assert.equal(f.events.includes("error-box"), false);
});

test("remote selection wins over an in-flight local startup and keeps the service for later", async () => {
  const f = await fixture({ deferredStartup: true });
  const picker = await f.openPicker();
  assert.equal((await f.request(picker, remote)).message, "Connected");
  const selected = f.windows.at(-1);
  f.finishStartup();
  await flush(); await flush();
  assert.equal(selected.isDestroyed(), false);
  assert.equal(f.windows.at(-1), selected);
  await f.openPicker();
  assert.equal((await f.request(picker, { kind: "state" })).canRetry, false);
  assert.equal((await f.request(picker, { kind: "local" })).message, "Connected");
  assert.equal(f.services(), 1);
  assert.equal(selected.isDestroyed(), true);
  assert.equal(f.windows.at(-1).webContents.mainFrame.url, `${local.origin}/#token=${local.token}`);
});

test("a late local startup failure does not mark the selected remote workspace failed", async () => {
  const f = await fixture({ deferredStartup: true });
  const picker = await f.openPicker();
  assert.equal((await f.request(picker, remote)).message, "Connected");
  const selected = f.windows.at(-1);
  f.failStartup(new Error("local startup failed"));
  await flush(); await flush();
  await f.openPicker();
  const state = await f.request(picker, { kind: "state" });
  assert.equal(state.current, "https://studio.example");
  assert.equal(state.canRetry, false);
  assert.equal(state.problem, false);
  assert.equal(selected.isDestroyed(), false);
  assert.equal(f.events.includes("quit"), false);
});

for (const fails of [false, true]) {
  test(`remote selection waits for initial local renderer ${fails ? "failure" : "success"}`, async () => {
    const f = await fixture({ deferredLoad: true });
    const first = f.windows[0], picker = await f.openPicker();
    const pending = f.request(picker, remote);
    await flush();
    assert.equal(first.isDestroyed(), false);
    if (fails) f.failLoad(new Error("local renderer failed"));
    else f.finishLoad();
    assert.equal((await pending).message, "Connected");
    assert.equal(first.isDestroyed(), true);
    const selected = f.windows.at(-1);
    assert.equal(selected.webContents.mainFrame.url, `https://studio.example/#token=${"r".repeat(32)}`);
    assert.equal(selected.isVisible(), true);
    assert.equal(f.events.includes("quit"), false);
    await f.openPicker();
    assert.equal((await f.request(picker, { kind: "local" })).message, "Connected");
    assert.equal(f.services(), 1);
    assert.equal(f.windows.at(-1).webContents.mainFrame.url, `${local.origin}/#token=${local.token}`);
  });
}

test("a failed remote replacement after initial load keeps the local workspace", async () => {
  const f = await fixture({ deferredLoad: true, failSecondProbe: true });
  const first = f.windows[0], picker = await f.openPicker();
  const pending = f.request(picker, remote);
  await flush();
  f.finishLoad();
  const state = await pending;
  assert.match(state.message, /Sign-in required/);
  assert.equal(state.current, local.origin);
  assert.equal(first.isDestroyed(), false);
  assert.equal(first.isVisible(), true);
  assert.equal(first.webContents.mainFrame.url, `${local.origin}/#token=${local.token}`);
});

test("a stalled initial window is destroyed and the selected remote opens despite a late load", async () => {
  const f = await fixture({ deferredLoad: true });
  const first = f.windows[0], picker = await f.openPicker();
  const pending = f.request(picker, remote);
  await flush();
  f.expireLoad();
  assert.equal((await pending).message, "Connected");
  const selected = f.windows.at(-1);
  assert.equal(first.isDestroyed(), true);
  assert.equal(selected.isVisible(), true);
  assert.equal(selected.webContents.mainFrame.url, `https://studio.example/#token=${"r".repeat(32)}`);
  f.finishLoad();
  await flush(); await flush();
  assert.equal(first.isVisible(), false);
  assert.equal(selected.isDestroyed(), false);
  assert.equal((await f.request(picker, { kind: "state" })).current, "https://studio.example");
});


test("a lost service is attached again with a new session link and the same bridge token re-registered", async () => {
  const f = await fixture();
  assert.equal(f.services(), 1);
  assert.equal(f.cliOptions[0].bridgeToken, "bridge-secret");
  f.loseService(); // the owned service crashed: launchd restarted it with a new session token
  for (let i = 0; i < 10 && f.services() < 2; i++) await flush();
  await flush(); await flush();
  assert.equal(f.services(), 2, "the app attached again");
  assert.equal(f.cliOptions[1].bridgeToken, "bridge-secret", "the bridge token goes to the new attach");
  assert.ok(f.windows.at(-1).webContents.mainFrame.url.startsWith("http://127.0.0.1:4567"), "the new session link is loaded");
  assert.ok(f.events.filter((event) => event === "bridge-start").length >= 2, "the browser bridge starts again");
  assert.deepEqual(f.waits, []);
  assert.equal(f.events.includes("error-box"), false);
});

test("re-attach retries with bounded backoff before it gives up", async () => {
  const f = await fixture({ reattachFailures: 2 });
  f.loseService();
  for (let i = 0; i < 40 && f.waits.length < 2; i++) await flush();
  for (let i = 0; i < 10; i++) await flush();
  assert.deepEqual(f.waits, [5000, 10000]);
  assert.ok(f.windows.at(-1).webContents.mainFrame.url.startsWith("http://127.0.0.1:4567"));
});

test("when every re-attach fails the connection screen opens with Try Again", async () => {
  const f = await fixture({ reattachFailures: 99 });
  f.loseService();
  for (let i = 0; i < 80 && f.waits.length < 4; i++) await flush();
  for (let i = 0; i < 10; i++) await flush();
  assert.deepEqual(f.waits, [5000, 10000, 20000, 40000]);
  assert.equal(f.services(), 6, "one attempt now and one after each delay");
  const picker = f.windows.find((window) => window.options.webPreferences.preload.endsWith("workspace-picker-preload.cjs"));
  assert.ok(picker && picker.isVisible(), "the connection screen is shown");
  assert.equal((await f.request(picker, { kind: "back" })).canRetry, true, "it offers Try Again");
});
