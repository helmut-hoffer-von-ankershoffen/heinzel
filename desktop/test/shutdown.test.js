import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import test from "node:test";
import { createOwnedCli } from "../src/owned-cli.js";
import { hideWindows, quitLogLine, showWindows, stopWithReport } from "../src/shutdown.js";

function attachedService() {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stdin = new PassThrough();
  child.exitCode = null;
  child.signalCode = null;
  child.signals = [];
  child.kill = (signal) => { child.signals.push(signal); return true; };
  const owned = createOwnedCli({ spawn: () => child, parse: () => ({ origin: "http://127.0.0.1:1234", token: "fake" }) });
  const start = owned.start();
  child.stdout.write("ready\n");
  child.exitCode = 0;
  child.emit("close", 0, null);
  return { owned, child, start };
}

test("quitting detaches from the owned service: no signal, no report, the service keeps running", async () => {
  const { owned, child, start } = attachedService();
  await start;
  let reports = 0;
  const result = await stopWithReport(owned, () => { reports += 1; });
  assert.equal(result.outcome, "detached");
  assert.equal(reports, 0);
  assert.deepEqual(child.signals, []);
});

test("clean and not-started outcomes do not report uncertain shutdown", async () => {
  for (const result of [{ outcome: "graceful" }, { outcome: "detached" }, { outcome: "not-started" }, { outcome: "already-exited", code: 0, signal: null }]) {
    let reports = 0;
    assert.equal(await stopWithReport({ stop: async () => result }, () => { reports += 1; }), result);
    assert.equal(reports, 0);
  }
});

test("reports an already-exited service when its exit was abnormal", async () => {
  let reports = 0;
  await stopWithReport(
    { stop: async () => ({ outcome: "already-exited", code: 2, signal: null, processError: false }) },
    () => { reports += 1; },
  );
  assert.equal(reports, 1);
});

test("refresh starts only after the lease closes with the fixed detached command", async () => {
  const { refreshAfterQuit } = await import("../src/shutdown.js");
  const gate = new EventEmitter();
  gate.stdin = { end: () => {} };
  const calls = [];
  let unrefed = false;
  const child = new EventEmitter();
  child.unref = () => { unrefed = true; };
  const result = refreshAfterQuit({ eligible: true, gate, executable: () => "/owned/hz", spawn: (...args) => { calls.push(args); return child; } });
  assert.equal(calls.length, 0);
  gate.emit("close");
  assert.equal(await result, true);
  assert.deepEqual(calls, [["/owned/hz", ["app", "refresh"], { detached: true, stdio: "ignore", shell: false }]]);
  assert.equal(unrefed, true);
});

test("refresh is skipped without eligibility, verified executable, or lease release", async () => {
  const { refreshAfterQuit } = await import("../src/shutdown.js");
  const gate = new EventEmitter();
  gate.stdin = { end: () => {} };
  let calls = 0;
  const spawn = () => { calls++; };
  assert.equal(await refreshAfterQuit({ eligible: false, gate, executable: () => "/owned/hz", spawn }), false);
  assert.equal(await refreshAfterQuit({ eligible: true, gate, executable: () => { throw new Error(); }, spawn }), false);
  assert.equal(await refreshAfterQuit({ eligible: true, gate, executable: () => "/owned/hz", spawn, timeoutMs: 2 }), false);
  assert.equal(calls, 0);
});


test("refresh eligibility excludes fixtures, signals and unclean or incomplete sessions", async () => {
  const { shouldRefresh } = await import("../src/shutdown.js");
  const healthy = { packaged: true, platform: "darwin", fixture: false, serviceStarted: true, unexpectedExit: false, signal: false, outcome: "graceful" };
  assert.equal(shouldRefresh(healthy), true);
  assert.equal(shouldRefresh({ ...healthy, outcome: "detached" }), true);
  for (const change of [{ packaged: false }, { platform: "linux" }, { fixture: true }, { serviceStarted: false },
    { unexpectedExit: true }, { signal: true }, { outcome: "forced" }, { outcome: "abnormal" },
    { outcome: "already-exited" }, { outcome: "not-started" }]) {
    assert.equal(shouldRefresh({ ...healthy, ...change }), false);
  }
});

test("quitting hides only visible live windows and shows exactly those again", () => {
  const calls = [];
  const fake = (name, { visible = true, destroyed = false, throws = false } = {}) => ({
    isDestroyed: () => destroyed,
    isVisible: () => { if (throws) throw new Error("gone"); return visible; },
    hide: () => calls.push(`hide ${name}`),
    show: () => calls.push(`show ${name}`),
  });
  const hidden = hideWindows([fake("a"), fake("b", { visible: false }), fake("c", { destroyed: true }), fake("d", { throws: true })]);
  assert.equal(hidden.length, 1);
  showWindows([...hidden, { isDestroyed: () => true, show: () => calls.push("show gone") }, { isDestroyed: () => { throw new Error("gone"); } }]);
  assert.deepEqual(calls, ["hide a", "show a"]);
});

test("an unclean stop during quit becomes one log line, not a dialog", () => {
  assert.deepEqual(JSON.parse(quitLogLine({ outcome: "forced", code: null, signal: "SIGKILL" })),
    { event: "desktop.quit.service_stop", outcome: "forced", code: null, signal: "SIGKILL" });
  assert.deepEqual(JSON.parse(quitLogLine()), { event: "desktop.quit.service_stop", code: null, signal: null });
});
