import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { hideWindows, quitLogLine, shouldRefresh, showWindows, stopWithReport } from "../src/shutdown.js";
import { createPublicMediaHandler } from "../src/public-media.js";
import { installSignalQuit } from "../src/signal-quit.js";

const main = await readFile(new URL("../src/main.js", import.meta.url), "utf8");
const preload = await readFile(new URL("../src/preload.cjs", import.meta.url), "utf8");

test("startup acquires the single-instance guard before launching the owned CLI", () => {
  assert.ok(main.indexOf("instance.acquire(process.argv") < main.indexOf("app.whenReady()"));
  assert.match(main, /instance\.setConnection\(connection\)/);
  assert.match(main, /contextIsolation: true/);
  assert.match(main, /nodeIntegration: false/);
  assert.match(main, /sandbox: true/);
  assert.match(main, /activations\.set\(window\.webContents, guardUiNavigation\(window\.webContents, connection, \(url\) => shell\.openExternal\(url\)\)\)/);
  assert.match(main, /ipcMain\.handle\("hz:user-activation", \(event\) => \{\n    try \{\n      if \(isOwnedMainFrame\(event, \{/);
  assert.match(main, /openProductUi\(/);
  assert.match(main, /cancelled: \(\) => stopping \|\| quitting/);
  assert.doesNotMatch(main, /data:text\/html/);
  assert.match(preload, /lifecycle: \(\) => ipcRenderer\.invoke\("hz:lifecycle"\)/);
  assert.doesNotMatch(preload, /token|send\(|sendSync|process|require\("node:/);
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
  const exits = [], dialogs = [], events = [], warnings = [];
  let stops = 0;
  const window = {
    visible: true,
    isDestroyed: () => false,
    isVisible: () => window.visible,
    hide: () => { window.visible = false; events.push("hide"); },
    show: () => { window.visible = true; events.push("show"); },
  };
  const owned = {
    exited: () => new Promise(() => {}),
    stop: async () => {
      stops += 1;
      events.push(window.visible ? "stop while visible" : "stop");
      if (rejects) throw new Error("fixture stop failure");
      return result;
    },
  };
  runInNewContext(main.replace(/^import .*;\n/gm, "").replaceAll("import.meta.url", "moduleUrl"), {
    moduleUrl: new URL("../src/main.js", import.meta.url).href,
    process: proc, app,
    console: { ...console, warn: (line) => warnings.push(line) },
    BrowserWindow: { getAllWindows: () => [window] },
    dialog: { showErrorBox: (...args) => dialogs.push(args) },
    ipcMain: { handle() {} }, lifecycle: {}, path: {},
    Menu: {}, shell: {}, isAllowedHelpUrl: () => false, appMenuTemplate: () => [], menuScript: () => "", createAboutHandler: () => () => {}, createAboutLinkHandler: () => () => {},
    initializeDesktopIdentity: () => true,
    createInstanceLifecycle: () => ({ acquire: () => true }),
    createMiniBrowser: () => ({ handle() {}, closeAll() {} }),
    createLifecycleHandler: () => () => {}, createOwnedCli: () => owned,
    createPublicMediaHandler, stopWithReport, shouldRefresh, hideWindows, showWindows, quitLogLine,
    installSignalQuit: (options) => installSignalQuit({ ...options, setTimer: () => ({ unref() {} }) }),
  });
  return { proc, app, exits, dialogs, events, warnings, stops: () => stops };
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

for (const result of [
  { outcome: "graceful", code: 0, signal: null },
  { outcome: "abnormal", code: 2, signal: null },
  { outcome: "forced", code: null, signal: "SIGKILL" },
  { outcome: "already-exited", code: 1, signal: null },
]) {
  test(`manual Quit hides the window first and shows no crash alert for the ${result.outcome} stop`, async () => {
    const fixture = quitHarness(result);
    fixture.app.quit();
    await new Promise(setImmediate);
    assert.equal(fixture.stops(), 1);
    assert.deepEqual(fixture.events, ["hide", "stop"], "No window may repaint the disconnected page while the service stops");
    assert.deepEqual(fixture.exits, [0]);
    assert.deepEqual(fixture.dialogs, [], "A deliberate quit is never reported as a crash");
    assert.deepEqual(fixture.warnings.map((line) => JSON.parse(line).outcome), result.outcome === "graceful" ? [] : [result.outcome]);
  });
}


test("manual Quit keeps the shell open when the owned child cannot be confirmed stopped", async () => {
  const fixture = quitHarness({}, true);
  fixture.app.quit();
  await new Promise(setImmediate);
  assert.equal(fixture.stops(), 1);
  assert.deepEqual(fixture.exits, []);
  assert.equal(fixture.dialogs.length, 1);
  assert.equal(fixture.dialogs[0][0], "Heinzelmännchen is still running");
  assert.deepEqual(fixture.events, ["hide", "stop", "show"], "The window comes back so Quit can be tried again");
  fixture.app.quit();
  await new Promise(setImmediate);
  assert.equal(fixture.stops(), 2, "A manual retry is still available");
  assert.deepEqual(fixture.exits, []);
});
