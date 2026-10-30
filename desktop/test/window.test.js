import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

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
  assert.match(main, /return ownedCli\.start\(\)\.then\(\(connection\) =>/);
  assert.match(main, /showErrorBox\("Heinzelmännchen could not open", message\)/);
});

test("Quit stops the owned CLI without adding background integration", () => {
  assert.match(main, /app\.on\("before-quit", \(event\) =>/);
  assert.match(main, /stopWithReport\(ownedCli,/);
  assert.match(main, /window\.on\("closed", \(\) => \{ if \(!quitting\) app\.quit\(\); \}\)/);
  assert.doesNotMatch(main, /setLoginItemSettings|systemPreferences|Notification/);
});
