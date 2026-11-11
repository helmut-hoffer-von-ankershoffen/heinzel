import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { shouldRefresh, stopWithReport } from "../src/shutdown.js";
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
  assert.match(main, /guardUiNavigation\(window\.webContents, connection\)/);
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
    ipcMain: { handle() {} }, lifecycle: {},
    initializeDesktopIdentity: () => true,
    createInstanceLifecycle: () => ({ acquire: () => true }),
    createMiniBrowser: () => ({ handle() {}, closeAll() {} }),
    createLifecycleHandler: () => () => {}, createOwnedCli: () => owned,
    createPublicMediaHandler, stopWithReport, shouldRefresh,
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
