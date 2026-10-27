import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const main = await readFile(new URL("../src/main.js", import.meta.url), "utf8");
const preload = await readFile(new URL("../src/preload.cjs", import.meta.url), "utf8");

test("startup acquires the single-instance guard and validates a connection before readiness", () => {
  assert.ok(main.indexOf("instance.acquire(process.argv") < main.indexOf("await app.whenReady()"));
  assert.match(main, /contextIsolation: true/);
  assert.match(main, /nodeIntegration: false/);
  assert.match(main, /sandbox: true/);
  assert.match(main, /setWindowOpenHandler\(\(\) => \(\{ action: "deny" \}\)\)/);
  assert.match(preload, /lifecycle: \(\) => ipcRenderer\.invoke\("hz:lifecycle"\)/);
  assert.doesNotMatch(preload, /token|send\(|sendSync|process|require\("node:/);
});

test("shutdown has no child process or autostart integration to leave running", () => {
  assert.match(main, /app\.on\("before-quit", \(\) => \{ quitting = true; \}\)/);
  assert.match(main, /window\.on\("closed", \(\) => \{ if \(!quitting\) app\.quit\(\); \}\)/);
  assert.doesNotMatch(main, /child_process|spawn\(|execFile|setLoginItemSettings|systemPreferences|Notification/);
});
