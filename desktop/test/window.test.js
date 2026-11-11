import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { shouldRefresh, stopWithReport } from "../src/shutdown.js";
import { installSignalQuit } from "../src/signal-quit.js";

const main = await readFile(new URL("../src/main.js", import.meta.url), "utf8");
const preload = await readFile(new URL("../src/preload.cjs", import.meta.url), "utf8");

test("startup acquires the single-instance guard before launching the owned CLI", () => {
  assert.ok(main.indexOf("instance.acquire(process.argv") < main.indexOf("app.whenReady()"));
  assert.match(main, /instance\.setConnection\(connection\)/);
  assert.match(main, /contextIsolation: true/);
  assert.match(main, /nodeIntegration: false/);
  assert.match(main, /sandbox: true/);
  assert.match(main, /guardUiNavigation\(window\.webContents, connection\)/);
  assert.match(main, /handleExternalActions\(window\.webContents,.*\(url\) => shell\.openExternal\(url\)\)/);
  assert.match(main, /openProductUi\(/);
  assert.match(main, /cancelled: \(\) => stopping \|\| quitting/);
  assert.doesNotMatch(main, /data:text\/html/);
  assert.match(preload, /lifecycle: \(\) => ipcRenderer\.invoke\("hz:lifecycle"\)/);
  assert.doesNotMatch(preload, /token|send\(|sendSync|process|require\("node:/);
});

test("the isolated preload hands off only trusted external anchor clicks from the main frame", async () => {
  const handlers = new Map();
  const invocations = [];
  const emitted = [];
  const origin = "http://127.0.0.1:30123";
  const window = { location: { origin } };
  const navigator = { userActivation: { isActive: true } };
  class Anchor {
    constructor(href, action = null) { Object.assign(this, { href, action, target: "_blank" }); }
    get origin() { return new URL(this.href).origin; }
    get protocol() { return new URL(this.href).protocol; }
    closest() { return this; }
    hasAttribute() { return false; }
    getAttribute() { return this.action; }
    dispatchEvent(event) { emitted.push(event.type); }
  }
  const context = {
    window, navigator, Element: Anchor, HTMLAnchorElement: Anchor, Event,
    document: { addEventListener: (type, handler, capture = false) => handlers.set(`${type}:${capture}`, handler) },
    require: () => ({ contextBridge: { exposeInMainWorld() {} }, ipcRenderer: {
      invoke: async (...args) => { invocations.push(args); return true; },
    } }),
  };
  window.top = {};
  runInNewContext(preload, { ...context });
  assert.equal(handlers.size, 0);
  window.top = window;
  runInNewContext(preload, { ...context });
  const ordinary = new Anchor("https://example.com/path");
  const provider = new Anchor("https://www.youtube.com/watch?v=tRsQsTMvPNg", "watch-video");
  const fire = (target, fields = {}) => {
    const event = { type: "click", isTrusted: true, button: 0, target, defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; }, ...fields };
    handlers.get(`${event.type}:true`)(event);
    handlers.get(`${event.type}:false`)(event);
    return event.defaultPrevented;
  };
  assert.equal(fire(ordinary, { isTrusted: false }), false);
  assert.equal(fire(ordinary, { defaultPrevented: true }), true);
  assert.equal(fire(new Anchor(origin)), false);
  navigator.userActivation.isActive = false;
  assert.equal(fire(ordinary), false);
  navigator.userActivation.isActive = true;
  assert.equal(fire(ordinary, { type: "auxclick", button: 2 }), false);
  assert.equal(fire(ordinary, { type: "auxclick", button: 1 }), true);
  assert.equal(fire(provider), true);
  assert.deepEqual(invocations, [
    ["hz:open-external", null, ordinary.href],
    ["hz:open-external", "watch-video", provider.href],
  ]);
  await Promise.resolve();
  assert.deepEqual(emitted, ["hz:external-opened"]);
});

test("Electron readiness starts the CLI and reports startup errors", () => {
  assert.doesNotMatch(main, /await\s+app\.whenReady\(\)/);
  assert.match(main, /void app\.whenReady\(\)\.then\(\(\) => \{/);
  assert.ok(main.indexOf("acquireDesktopGate()") < main.indexOf("return ownedCli.start()"));
  assert.match(main, /showErrorBox\("Heinzelmännchen could not open", message\)/);
});

test("Quit stops the owned CLI without adding background integration", () => {
  assert.match(main, /app\.on\("before-quit", \(event\) =>/);
  assert.match(main, /stopWithReport\(ownedCli,/);
  assert.doesNotMatch(main, /setLoginItemSettings|systemPreferences|Notification/);
});


// Execute main's real quit handler with mocked Electron and an owned-CLI result: no native app or process starts.
function quitHarness(result, rejects = false) {
  const proc = Object.assign(new EventEmitter(), { argv: ["node", "fixture"], env: {}, platform: "darwin" });
  const app = Object.assign(new EventEmitter(), {
    isPackaged: false,
    whenReady: () => new Promise(() => {}),
    quit: () => {
      let prevented = false;
      app.emit("before-quit", { preventDefault: () => { prevented = true; } });
      if (!prevented) exits.push(0);
    },
    exit: (code) => exits.push(code),
  });
  const exits = [], dialogs = [];
  let stops = 0;
  const owned = {
    exited: () => new Promise(() => {}),
    stop: async () => { stops += 1; if (rejects) throw new Error("fixture stop failure"); return result; },
  };
  runInNewContext(main.replace(/^import .*;\n/gm, "").replaceAll("import.meta.url", "moduleUrl"), {
    moduleUrl: new URL("../src/main.js", import.meta.url).href,
    process: proc, app,
    dialog: { showErrorBox: (...args) => dialogs.push(args) },
    ipcMain: { handle() {} }, lifecycle: {}, path: {},
    Menu: {}, shell: {}, isAllowedHelpUrl: () => false, appMenuTemplate: () => [], menuScript: () => "", createAboutHandler: () => () => {},
    initializeDesktopIdentity: () => true,
    createInstanceLifecycle: () => ({ acquire: () => true }),
    createMiniBrowser: () => ({ handle() {}, closeAll() {} }),
    createLifecycleHandler: () => () => {}, createOwnedCli: () => owned,
    stopWithReport, shouldRefresh,
    installSignalQuit: (options) => installSignalQuit({ ...options, setTimer: () => ({ unref() {} }) }),
  });
  return { proc, app, exits, dialogs, stops: () => stops };
}

for (const signal of ["SIGTERM", "SIGINT", "SIGHUP"]) {
  for (const [outcome, code] of [["graceful", 0], ["abnormal", 1], ["forced", 1], ["rejected", 1]]) {
    test(`${signal} returns ${code} for ${outcome} owned-child shutdown without a GUI dialog`, async () => {
      const fixture = quitHarness({ outcome }, outcome === "rejected");
      fixture.proc.emit(signal, signal);
      await new Promise(setImmediate);
      assert.equal(fixture.stops(), 1);
      assert.deepEqual(fixture.exits, [code]);
      assert.deepEqual(fixture.dialogs, []);
    });
  }
}

test("manual Quit retains its visible report for an unclean stopped child", async () => {
  const fixture = quitHarness({ outcome: "forced" });
  fixture.app.quit();
  await new Promise(setImmediate);
  assert.equal(fixture.stops(), 1);
  assert.deepEqual(fixture.exits, [0]);
  assert.equal(fixture.dialogs.length, 1);
});


test("manual Quit keeps the shell open when the owned child cannot be confirmed stopped", async () => {
  const fixture = quitHarness({}, true);
  fixture.app.quit();
  await new Promise(setImmediate);
  assert.equal(fixture.stops(), 1);
  assert.deepEqual(fixture.exits, []);
  assert.equal(fixture.dialogs.length, 1);
  fixture.app.quit();
  await new Promise(setImmediate);
  assert.equal(fixture.stops(), 2, "A manual retry is still available");
  assert.deepEqual(fixture.exits, []);
});
