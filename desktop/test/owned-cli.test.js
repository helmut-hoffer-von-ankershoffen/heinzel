import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import test from "node:test";
import { createOwnedCli } from "../src/owned-cli.js";

const url = "http://127.0.0.1:43817/#token=abcdefghijklmnopqrstuvwxyz0123456789";

function makeChild() {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.closed = false;
  child.once("close", () => { child.closed = true; });
  child.exitCode = null;
  child.signalCode = null;
  child.kills = [];
  child.kill = (signal) => {
    child.kills.push(signal);
    if (signal === "SIGTERM") {
      child.signalCode = signal;
      child.emit("exit", null, signal);
      child.emit("close", null, signal);
    }
    return true;
  };
  return child;
}

test("launches the installed CLI with fixed arguments and returns its verified URL", async () => {
  const child = makeChild();
  let spawned;
  const owned = createOwnedCli({ spawn: (...args) => { spawned = args; return child; } });
  const started = owned.start();
  assert.deepEqual(spawned, ["hz", ["ui", "--port", "0", "--no-browser"], {
    shell: false,
    windowsHide: true,
    stdio: ["ignore", "pipe", "ignore"],
  }]);
  child.stdout.write(`${url}\n`);
  const connection = await started;
  assert.equal(connection.origin, "http://127.0.0.1:43817");
  assert.equal(connection.token, "abcdefghijklmnopqrstuvwxyz0123456789");
  assert.equal(owned.currentConnection(), connection);
  assert.equal(JSON.stringify(connection).includes(url), false);
  assert.equal(child.stdout.listenerCount("data"), 0);
  assert.equal(child.listenerCount("error"), 0);
  assert.equal(child.listenerCount("exit"), 1);
});

test("reports service exit after startup and exposes process liveness", async () => {
  const child = makeChild();
  const owned = createOwnedCli({ spawn: () => child });
  const exited = owned.exited();
  const started = owned.start();
  child.stdout.write(`${url}\n`);
  await started;
  assert.equal(owned.isRunning(), true);

  child.exitCode = 1;
  child.emit("exit", 1, null);
  assert.deepEqual(await exited, { code: 1, signal: null });
  assert.equal(owned.isRunning(), false);
  child.emit("close", 1, null);
});

test("rejects malformed stdout without exposing the received credential in its error", async () => {
  const child = makeChild();
  const owned = createOwnedCli({ spawn: () => child });
  const started = owned.start();
  child.stdout.write("http://attacker.invalid/#token=secret\n");
  await assert.rejects(started, (error) => {
    assert.match(error.message, /could not start/);
    assert.equal(error.message.includes("secret"), false);
    return true;
  });
});

test("reports early process exit and startup timeout", async () => {
  const exited = makeChild();
  const onExit = createOwnedCli({ spawn: () => exited });
  const exitPromise = onExit.start();
  exited.emit("exit", 1, null);
  await assert.rejects(exitPromise, /could not start/);

  const timedOut = makeChild();
  const onTimeout = createOwnedCli({ spawn: () => timedOut, startupTimeoutMs: 5 });
  await assert.rejects(onTimeout.start(), /too long to start/);
  assert.deepEqual(await onTimeout.exited(), { code: null, signal: null, failedToStart: true });
  await onTimeout.stop();
  assert.deepEqual(timedOut.kills, ["SIGTERM"]);
});

test("shutdown signals only the owned child and escalates after the grace period", async () => {
  const child = makeChild();
  const owned = createOwnedCli({ spawn: () => child, stopTimeoutMs: 5 });
  const starting = owned.start();
  child.stdout.write(`${url}\n`);
  await starting;
  await owned.stop();
  assert.deepEqual(child.kills, ["SIGTERM"]);

  const unresponsive = makeChild();
  unresponsive.kill = (signal) => { unresponsive.kills.push(signal); return true; };
  const stubborn = createOwnedCli({ spawn: () => unresponsive, stopTimeoutMs: 5, forceStopTimeoutMs: 5 });
  const pending = stubborn.start();
  unresponsive.stdout.write(`${url}\n`);
  await pending;
  await assert.rejects(stubborn.stop(), /did not stop/);
  assert.equal(stubborn.isRunning(), true);
  unresponsive.signalCode = "SIGKILL";
  unresponsive.emit("exit", null, "SIGKILL");
  unresponsive.emit("close", null, "SIGKILL");
  assert.deepEqual(unresponsive.kills, ["SIGTERM", "SIGKILL"]);

  const noChild = createOwnedCli({ spawn: () => { throw new Error("must not spawn"); } });
  await noChild.stop();
});

test("handles asynchronously delivered child errors through close during shutdown escalation", async () => {
  const child = makeChild();
  child.kill = (signal) => {
    child.kills.push(signal);
    process.nextTick(() => child.emit("error", new Error(`signal failed: ${signal}`)));
    if (signal === "SIGKILL") {
      setTimeout(() => {
        child.signalCode = signal;
        child.emit("exit", null, signal);
        child.emit("close", null, signal);
      }, 0);
    }
    return false;
  };
  const owned = createOwnedCli({ spawn: () => child, stopTimeoutMs: 5, forceStopTimeoutMs: 50 });
  const starting = owned.start();
  child.stdout.write(`${url}\n`);
  await starting;

  await assert.doesNotReject(owned.stop());
  assert.deepEqual(child.kills, ["SIGTERM", "SIGKILL"]);
  assert.equal(child.listenerCount("error"), 0);
});

test("can retry shutdown after a child did not close following the forced signal", async () => {
  const child = makeChild();
  child.kill = (signal) => {
    child.kills.push(signal);
    if (signal === "SIGTERM" && child.kills.filter((entry) => entry === "SIGTERM").length > 1) {
      child.signalCode = signal;
      child.emit("exit", null, signal);
      child.emit("close", null, signal);
    }
    return true;
  };
  const owned = createOwnedCli({ spawn: () => child, stopTimeoutMs: 2, forceStopTimeoutMs: 2 });
  const starting = owned.start();
  child.stdout.write(`${url}\n`);
  await starting;
  await assert.rejects(owned.stop(), /did not stop/);
  await owned.stop();
  assert.deepEqual(child.kills, ["SIGTERM", "SIGKILL", "SIGTERM"]);
});
