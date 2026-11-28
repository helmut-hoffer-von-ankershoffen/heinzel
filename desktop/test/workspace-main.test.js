import assert from "node:assert/strict";
import { attachClientHeader } from "../src/client-identity.js";
import { EventEmitter } from "node:events";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { mainScript } from "./main-source.js";
import test from "node:test";
import { appMenuTemplate } from "../src/app-menu.js";
import { createInstanceLifecycle } from "../src/instance-lifecycle.js";
import { createLifecycleHandler } from "../src/lifecycle-ipc.js";
import { createWorkspaceController, isPickerFrame } from "../src/workspace-controller.js";
import { guardUiNavigation, openProductUi } from "../src/ui-navigation.js";
import { stopWithReport } from "../src/shutdown.js";
import { createWorkspaceMonitor } from "../src/workspace-monitor.js";
import { workspaceName } from "../src/workspace-directory.js";
import { isOwnedMainFrame } from "../src/public-media.js";
import { createMiniBrowser } from "../src/mini-browser.js";
import { createMediaPlayer } from "../src/media-player.js";
import { createAlbumTransport, filmURL, installWorkspaceFilm } from "../src/album-transport.js";
import { registerAlbumScheme } from "../src/album-scheme.js";

const moduleUrl = new URL("../src/main.js", import.meta.url).href;
const source = mainScript();
const local = { origin: "http://127.0.0.1:1234", token: "l".repeat(32) };
const remote = { kind: "link", link: `https://studio.example/#token=${"r".repeat(32)}` };
const flush = () => new Promise(setImmediate);

async function fixture({ startupFails = false, failSecondProbe = false, initialLoadFails = false, deferredStartup = false, deferredLoad = false, closeChildWindows = false } = {}) {
  const windows = [], sessions = [], handlers = new Map(), events = [];
  let menu, rejected = false, focused;
  let remoteProbes = 0, outage = false;
  let finishLoad, failLoad, expireLoad;
  const load = new Promise((resolve, reject) => { finishLoad = resolve; failLoad = reject; });
  class Window extends EventEmitter {
    constructor(options) {
      super(); this.options = options; this.destroyed = false; this.visible = false;
      const session = options.webPreferences.session ?? makeSession(options.webPreferences.partition);
      const contents = Object.assign(new EventEmitter(), {
        id: windows.length + 1, session,
        mainFrame: { processId: 1, frameToken: String(windows.length + 1), url: "", isDestroyed: () => this.destroyed, detached: false },
        userAgent: "Electron/fixture", getUserAgent() { return this.userAgent; }, setUserAgent(value) { this.userAgent = value; },
        isDestroyed: () => this.destroyed, setWindowOpenHandler() {}, send() {}, getZoomFactor: () => 1,
      });
      this.webContents = contents; this.contentView = { addChildView() {}, removeChildView() {} }; windows.push(this);
    }
    async loadURL(url) {
      this.filmRegisteredAtLoad = this.webContents.session.protocolHandlers.has("heinzel-album");
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
    destroy() {
      this.destroyed = true;
      if (closeChildWindows) {
        for (const child of Window.getAllWindows()) if (child.options.parent === this) child.destroy();
      }
      this.webContents.emit("destroyed"); this.emit("closed");
    }
    static getAllWindows() { return windows.filter((window) => !window.destroyed); }
    static fromWebContents(contents) { return windows.find((window) => window.webContents === contents); }
    static getFocusedWindow() { return focused; }
  }
  function makeSession(partition) {
    const session = { partition,
      protocolHandlers: new Map(),
      protocol: { handle: (scheme, handler) => session.protocolHandlers.set(scheme, handler) },
      setProxy: async (value) => { session.proxy = value; },
      setPermissionCheckHandler: (value) => { session.permission = value; },
      setPermissionRequestHandler: (value) => { session.requestPermission = value; },
      setDisplayMediaRequestHandler() {},
      setDevicePermissionHandler() {}, setBluetoothPairingHandler() {},
      webRequest: { onBeforeRequest(handler) { session.beforeRequest = handler; }, onBeforeSendHeaders(filter, handler) { session.clientHeader = { filter, handler }; } }, on() {},
      clearStorageData: async () => { session.cleared = true; }, clearCache: async () => {},
    };
    sessions.push(session); return session;
  }
  const app = Object.assign(new EventEmitter(), {
    isPackaged: false, name: "Heinzelmännchen", requestSingleInstanceLock: () => true,
    whenReady: () => Promise.resolve(), getPath: () => "/fixture", getAppPath: () => "/fixture/application",
    getVersion: () => "1.0.0", setAboutPanelOptions() {},
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
  const losses = [];
  const cli = () => {
    serviceCount++;

    const connection = serviceCount === 1 ? local : { origin: "http://127.0.0.1:4567", token: "n".repeat(32) };
    let lose;
    const exited = new Promise((resolve) => { lose = resolve; });
    losses.push(() => { running = false; lose({ code: null, signal: null, serviceLost: true }); });
    return { exited: () => exited, isRunning: () => running,
      start: async () => {
        events.push("start-local");
        if (deferredStartup && serviceCount === 1) await startup;
        if (startupFails) throw new Error("startup");
        running = true; return connection;
      },
      stop: async () => { events.push("stop-local"); running = false; return { outcome: "graceful" }; },
    };
  };
  runInNewContext(source, {
    protocol: { registerSchemesAsPrivileged() {} }, registerAlbumScheme, createAlbumTransport, installWorkspaceFilm,
    probeWorkspaceRenderer: async (_window, connection) => {
      events.push(["renderer-verify", connection.origin]);
      if (outage) throw new Error("unreachable");
      return { version: "1.0.0", instance: "fixture" };
    },
    createWorkspaceMonitor, workspaceName, isOwnedMainFrame, AbortSignal, AbortController,
    Notification: { isSupported: () => true }, net: { fetch: async () => ({}), isOnline: () => true },
    powerMonitor: new EventEmitter(), readWorkspaceFacts: async () => ({}), verifyWorkspaceRenderer: async () => {},
    moduleUrl, URL, path, fileURLToPath, randomUUID: () => String(sessions.length), console,
    process: Object.assign(new EventEmitter(), { argv: [], env: {}, platform: "darwin", getSystemVersion: () => "26.1" }),
    app, BrowserWindow: Window, dialog: { showErrorBox: () => events.push("error-box") },
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    Menu: { buildFromTemplate: (value) => value, setApplicationMenu: (value) => { menu = value; } },
    session: { fromPartition: makeSession }, safeStorage: {}, shell: { openExternal() {} },
    createWorkspaceDirectory: () => ({ list: () => [], connection: () => null, remember: () => true, forget: () => true }),
    attachClientHeader, machineName: () => "Air", createWorkspaceController, isPickerFrame, createInstanceLifecycle, createLifecycleHandler, guardUiNavigation,
    openProductUi: (options) => openProductUi(deferredLoad ? { ...options,
      schedule: (callback) => { if (!expireLoad) { expireLoad = callback; return null; } return setTimeout(callback, 15000); },
      unschedule: (timer) => { if (timer) clearTimeout(timer); },
    } : options),
    lifecycle: async (connection) => {
      events.push(["verify", connection.origin]);
      if (outage && !connection.remote) throw new Error("unreachable");
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

  });
  await flush(); await flush();
  const openPicker = async () => {
    menu[0].submenu.find((item) => item.label === "Workspaces…").click(); await flush();
    return Window.getAllWindows().find((window) => window.options.webPreferences.preload.endsWith("workspace-picker-preload.cjs"));
  };
  const request = (picker, value) => handlers.get("hz:workspace")({ sender: picker.webContents, senderFrame: picker.webContents.mainFrame }, value);
  const surface = (window, name, value) => handlers.get(name)({ sender: window.webContents, senderFrame: window.webContents.mainFrame }, value);
  return { windows, sessions, handlers, events, openPicker, request, surface, services: () => serviceCount,
    loseService: () => losses.at(-1)(),
    outage: (value) => { outage = value; }, reject: () => { rejected = true; }, stopLocal: () => { running = false; }, finishStartup, failStartup, finishLoad, failLoad,
    expireLoad: () => expireLoad() };
}

test("main switches local to remote and back with fresh sessions and no remote native authority", async () => {
  const f = await fixture();
  const first = f.windows[0];
  assert.equal(first.webContents.session.proxy.mode, "direct");
  assert.equal(first.webContents.getUserAgent(), "Electron/fixture HeinzelAlbum/1");
  let headers;
  first.webContents.session.clientHeader.handler({ url: `${local.origin}/api/workspace-version`,
    requestHeaders: { Authorization: "Bearer fixture" } }, (result) => { headers = result.requestHeaders; });
  assert.equal(headers["X-Heinzel-Client"], "flavor=electron;app=1.0.0;backend=unknown;machine=Air");
  const picker = await f.openPicker();
  const { application } = await f.request(picker, { kind: "state" });
  assert.deepEqual({ ...application }, { type: "Electron", version: "1.0.0", path: "/fixture/application", operatingSystem: "macOS 26.1" });
  assert.equal((await f.request(picker, remote)).message, "Connected");
  const remoteWindow = f.windows.at(-1);
  assert.equal(remoteWindow.webContents.getUserAgent(), "Electron/fixture");
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
  const reopened = await f.openPicker();
  await f.request(reopened, { kind: "local" });
  assert.equal(remoteWindow.isDestroyed(), true);
  assert.equal(remoteWindow.webContents.session.cleared, true);
  assert.equal(f.services(), 1);
  assert.equal(f.events.filter((event) => event === "bridge-start").length, 2);
  assert.equal(f.events.includes("quit"), false);
  assert.notEqual(f.windows.at(-1).webContents.session, first.webContents.session);
});

test("the local film loader exists at navigation and loses authority on workspace switch", async () => {
  const f = await fixture();
  const localWindow = f.windows[0];
  const { id, mainFrame, session } = localWindow.webContents;
  assert.equal(localWindow.filmRegisteredAtLoad, true);
  const details = { webContentsId: id, frame: mainFrame, url: filmURL, method: "GET", resourceType: "media" };
  const cancelled = () => {
    let cancel;
    session.beforeRequest(details, result => { cancel = result.cancel; });
    return cancel;
  };
  assert.equal(cancelled(), false);
  const picker = await f.openPicker();
  assert.equal(picker.filmRegisteredAtLoad, false);
  assert.equal((await f.request(picker, remote)).message, "Connected");
  assert.equal(f.windows.at(-1).filmRegisteredAtLoad, false);
  assert.equal(cancelled(), true);
  const response = await session.protocolHandlers.get("heinzel-album")(new Request(filmURL));
  assert.equal(response.status, 403);
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
  assert.equal(f.events.filter((event) => event === "bridge-start").length, 1, "failed candidate never stopped the bridge");
  assert.equal((await f.surface(first, "hz:player", { action: "state" })).ready, true);
  await f.surface(first, "hz:browser", { action: "navigate", url: "https://example.org/" });
});

test("returning to local restores Browser and player when macOS destroys the former workspace's sheet", async () => {
  const f = await fixture({ closeChildWindows: true });
  const first = f.windows[0], picker = await f.openPicker();
  await f.surface(first, "hz:browser", { action: "navigate", url: "https://example.com/" });
  await f.surface(first, "hz:player", { action: "open", video: "abcdefghijk" });
  await f.request(picker, remote);
  const reopened = await f.openPicker();
  await f.request(reopened, { kind: "local" });
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
  assert.match(state.message, /Connection restored/);
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
  const reopened = await f.openPicker();
  assert.equal((await f.request(reopened, { kind: "state" })).canRetry, false);
  assert.equal((await f.request(reopened, { kind: "local" })).message, "Connected");
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
  const reopened = await f.openPicker();
  const state = await f.request(reopened, { kind: "state" });
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
    const reopened = await f.openPicker();
    assert.equal((await f.request(reopened, { kind: "local" })).message, "Connected");
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


test("confirmed local loss shows its row immediately; recovery keeps the document and does not start a service", async () => {
  const f = await fixture(), first = f.windows[0];
  f.outage(true); f.loseService(); await flush();
  const picker = f.windows.at(-1);
  assert.equal(picker.isVisible(), true);
  assert.equal((await f.request(picker, { kind: "state" })).recovery.phase, "reconnecting");
  await f.request(picker, { kind: "pause", origin: local.origin });
  f.outage(false);
  await f.request(picker, { kind: "network" });
  assert.equal((await f.request(picker, { kind: "state" })).recovery.phase, "paused");
  await f.request(picker, { kind: "resume", origin: local.origin }); await flush();
  assert.equal(first.isDestroyed(), false);
  assert.equal(first.isVisible(), true);
  assert.equal(picker.isVisible(), false);
  assert.equal(f.services(), 1);
});

test("profile and native menu share a chooser and disconnect keeps the local service running", async () => {
  const f = await fixture(), first = f.windows[0], picker = await f.openPicker();
  assert.equal(await f.surface(first, "hz:workspace-menu", "name"), "This Mac");
  assert.equal(await f.surface(first, "hz:workspace-menu", "open"), true);
  assert.equal(f.windows.at(-1), picker);
  await f.request(picker, { kind: "disconnect", origin: local.origin }); await flush();
  const full = f.windows.at(-1);
  assert.equal((await f.request(full, { kind: "state" })).current, null);
  assert.equal(first.isDestroyed(), true);
  assert.equal(f.events.includes("stop-local"), false);
  await f.request(full, remote);
  const remoteWindow = f.windows.at(-1);
  assert.equal(await f.surface(remoteWindow, "hz:workspace-menu", "name"), "studio.example");
  await f.surface(remoteWindow, "hz:workspace-menu", "open"); await flush();
  const next = f.windows.at(-1);
  await f.request(next, { kind: "local" });
  assert.equal(f.services(), 1);
});
