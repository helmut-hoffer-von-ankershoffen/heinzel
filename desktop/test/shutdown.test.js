import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import test from "node:test";
import { createOwnedCli } from "../src/owned-cli.js";
import { stopWithReport } from "../src/shutdown.js";

async function ownedProcess(exitOn) {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.exitCode = null;
  child.signalCode = null;
  child.signals = [];
  child.kill = (signal) => {
    child.signals.push(signal);
    if (signal === exitOn) {
      child.exitCode = signal === "SIGTERM" ? 2 : null;
      child.signalCode = signal === "SIGKILL" ? signal : null;
      child.emit("exit", child.exitCode, child.signalCode);
      child.emit("close", child.exitCode, child.signalCode);
    }
    return true;
  };
  const owned = createOwnedCli({
    spawn: () => child,
    parse: () => ({ origin: "http://127.0.0.1:1234", token: "fake" }),
    stopTimeoutMs: 2,
    forceStopTimeoutMs: 5,
  });
  const start = owned.start();
  child.stdout.write("ready\n");
  await start;
  return { owned, child };
}

for (const [signal, outcome] of [["SIGTERM", "abnormal"], ["SIGKILL", "forced"]]) {
  test(`reports ${outcome} after the fake owned process closes`, async () => {
    const { owned, child } = await ownedProcess(signal);
    let reports = 0;
    const result = await stopWithReport(owned, () => { reports += 1; });
    assert.equal(result.outcome, outcome);
    assert.equal(reports, 1);
    assert.deepEqual(child.signals, signal === "SIGKILL" ? ["SIGTERM", "SIGKILL"] : ["SIGTERM"]);
  });
}

test("clean and not-started outcomes do not report uncertain shutdown", async () => {
  for (const result of [{ outcome: "graceful" }, { outcome: "not-started" }, { outcome: "already-exited", code: 0, signal: null }]) {
    let reports = 0;
    assert.equal(await stopWithReport({ stop: async () => result }, () => { reports += 1; }), result);
    assert.equal(reports, 0);
  }
});

test("a service that did not close rejects so the shell can remain open and retry", async () => {
  const { owned, child } = await ownedProcess("never");
  let reports = 0;
  await assert.rejects(stopWithReport(owned, () => { reports += 1; }), /did not stop/);
  assert.equal(reports, 0);
  child.kill = () => {
    child.exitCode = 0;
    child.emit("exit", 0, null);
    child.emit("close", 0, null);
    return true;
  };
  assert.equal((await stopWithReport(owned, () => { reports += 1; })).outcome, "graceful");
  assert.equal(reports, 0);
});

test("reports an already-exited service when its exit was abnormal", async () => {
  let reports = 0;
  await stopWithReport(
    { stop: async () => ({ outcome: "already-exited", code: 2, signal: null, processError: false }) },
    () => { reports += 1; },
  );
  assert.equal(reports, 1);
});
