import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { PINNED_NODE, checkNode } from "../ci/node-version.mjs";
import { installSignalQuit } from "../src/signal-quit.js";

function harness(options = {}) {
  const proc = new EventEmitter();
  const calls = { quit: [], exit: [], timers: [] };
  const timer = { unref() { this.unrefd = true; } };
  const installed = installSignalQuit({
    proc,
    quit: (signal) => calls.quit.push(signal),
    forceExit: (code) => calls.exit.push(code),
    setTimer: (fn, ms) => { calls.timers.push({ fn, ms }); return timer; },
    ...options,
  });
  return { proc, calls, installed, timer };
}

test("SIGTERM, SIGINT and SIGHUP each request one graceful quit", () => {
  for (const signal of ["SIGTERM", "SIGINT", "SIGHUP"]) {
    const { proc, calls, installed } = harness();
    proc.emit(signal, signal);
    proc.emit("SIGTERM", "SIGTERM");
    assert.deepEqual(calls.quit, [signal]);
    assert.equal(installed.received(), signal);
    assert.equal(calls.timers.length, 1);
  }
});

test("a quit that never completes is forced to exit non-zero at the deadline without keeping the loop alive", () => {
  const { proc, calls, timer } = harness({ deadlineMs: 1234 });
  proc.emit("SIGTERM", "SIGTERM");
  assert.equal(calls.timers[0].ms, 1234);
  assert.equal(timer.unrefd, true);
  assert.deepEqual(calls.exit, []);
  calls.timers[0].fn();
  assert.deepEqual(calls.exit, [1]);
});

test("nothing happens before a signal arrives", () => {
  const { calls, installed } = harness();
  assert.equal(installed.received(), null);
  assert.deepEqual(calls.quit, []);
});

test("CI requires exactly the pinned Node.js", () => {
  assert.equal(checkNode(PINNED_NODE, { CI: "true" }).ok, true);
  const newer = checkNode("26.12.1", { CI: "true" });
  assert.equal(newer.ok, false);
  assert.match(newer.message, new RegExp(PINNED_NODE.replaceAll(".", "\\.")));
  assert.equal(checkNode("26.12.1", { HZ_DESKTOP_STRICT_NODE: "1" }).ok, false);
});

test("local builds accept newer Node.js with a notice and reject older majors", () => {
  const newer = checkNode("26.12.1", {});
  assert.equal(newer.ok, true);
  assert.match(newer.warning, /26\.12\.1/);
  assert.equal(checkNode(PINNED_NODE, {}).warning, undefined);
  assert.equal(checkNode("22.11.0", {}).ok, false);
  assert.equal(checkNode("garbage", {}).ok, false);
});

test("the CI workflow pins the same Node.js as the packager", async () => {
  const workflow = await readFile(new URL("../../.github/workflows/ci.yml", import.meta.url), "utf8");
  const electron = workflow.slice(workflow.indexOf("\n  electron:"));
  assert.match(electron, new RegExp(`node-version: ${PINNED_NODE.replaceAll(".", "\\.")}`));
});
