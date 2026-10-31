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
    if (signal === "SIGINT") {
      child.exitCode = 0;
      child.emit("exit", 0, null);
      child.emit("close", 0, null);
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
  assert.equal(child.listenerCount("error"), 1);
  assert.equal(child.listenerCount("exit"), 1);
});

test("keeps process errors guarded after startup and reports unclean stop evidence", async () => {
  const child = makeChild();
  const owned = createOwnedCli({ spawn: () => child });
  const started = owned.start();
  child.stdout.write(`${url}\n`);
  await started;
  child.emit("error", new Error("private diagnostic must not escape"));
  assert.equal(owned.isRunning(), false);
  assert.deepEqual(await owned.exited(), { code: null, signal: null, processError: true });
  assert.deepEqual(await owned.stop(), { outcome: "abnormal", code: 0, signal: null, processError: true });
  assert.equal(child.listenerCount("error"), 0);
});

test("keeps a guarded listener after a rejected startup until the child closes", async () => {
  const child = makeChild();
  const owned = createOwnedCli({ spawn: () => child });
  const started = owned.start();
  child.stdout.write("invalid\n");
  await assert.rejects(started);
  child.emit("error", new Error("late process failure"));
  assert.equal(child.listenerCount("error"), 1);
  await owned.stop();
  assert.equal(child.listenerCount("error"), 0);
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
  assert.deepEqual(await onTimeout.stop(), {
    outcome: "graceful",
    code: 0,
    signal: null,
    processError: false,
  });
  assert.deepEqual(timedOut.kills, ["SIGINT"]);
});

test("recognizes a child already closed after a spawn error", async () => {
  const child = makeChild();
  const owned = createOwnedCli({ spawn: () => child });
  const starting = owned.start();
  child.emit("error", new Error("spawn failed"));
  await assert.rejects(starting, /could not start/);
  child.emit("close", null, null);
  delete child.closed;

  assert.deepEqual(await owned.stop(), {
    outcome: "already-exited",
    code: null,
    signal: null,
    processError: false,
  });
  assert.deepEqual(child.kills, []);
});

test("shutdown signals only the owned child and escalates after the grace period", async () => {
  const child = makeChild();
  const owned = createOwnedCli({ spawn: () => child, stopTimeoutMs: 5 });
  const starting = owned.start();
  child.stdout.write(`${url}\n`);
  await starting;
  assert.deepEqual(await owned.stop(), {
    outcome: "graceful",
    code: 0,
    signal: null,
    processError: false,
  });
  assert.deepEqual(child.kills, ["SIGINT"]);

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
  assert.deepEqual(unresponsive.kills, ["SIGINT", "SIGKILL"]);

  const noChild = createOwnedCli({ spawn: () => { throw new Error("must not spawn"); } });
  assert.deepEqual(await noChild.stop(), {
    outcome: "not-started",
    code: null,
    signal: null,
    processError: false,
  });
});

test("classifies shutdown from the observed exit when SIGKILL races with a clean exit", async () => {
  const child = makeChild();
  child.kill = (signal) => {
    child.kills.push(signal);
    if (signal === "SIGKILL") {
      child.exitCode = 0;
      child.emit("exit", 0, null);
      child.emit("close", 0, null);
      return false;
    }
    return true;
  };
  const owned = createOwnedCli({ spawn: () => child, stopTimeoutMs: 2, forceStopTimeoutMs: 50 });
  const starting = owned.start();
  child.stdout.write(`${url}\n`);
  await starting;

  assert.deepEqual(await owned.stop(), {
    outcome: "graceful",
    code: 0,
    signal: null,
    processError: false,
  });
  assert.deepEqual(child.kills, ["SIGINT", "SIGKILL"]);
});

test("classifies nonzero exit, signal exit and already-exited abnormal child", async (t) => {
  for (const scenario of [
    {
      name: "nonzero exit after interrupt",
      stop: (child) => {
        child.exitCode = 2;
        child.emit("exit", 2, null);
        child.emit("close", 2, null);
      },
      expected: { outcome: "abnormal", code: 2, signal: null, processError: false },
    },
    {
      name: "signal exit after interrupt",
      stop: (child) => {
        child.signalCode = "SIGTERM";
        child.emit("exit", null, "SIGTERM");
        child.emit("close", null, "SIGTERM");
      },
      expected: { outcome: "abnormal", code: null, signal: "SIGTERM", processError: false },
    },
  ]) {
    await t.test(scenario.name, async () => {
      const child = makeChild();
      child.kill = (signal) => {
        child.kills.push(signal);
        if (signal === "SIGINT") scenario.stop(child);
        return true;
      };
      const owned = createOwnedCli({ spawn: () => child });
      const started = owned.start();
      child.stdout.write(`${url}\n`);
      await started;

      assert.deepEqual(await owned.stop(), scenario.expected);
    });
  }

  await t.test("already-exited abnormal child retains its exit evidence", async () => {
    const child = makeChild();
    const owned = createOwnedCli({ spawn: () => child });
    const started = owned.start();
    child.stdout.write(`${url}\n`);
    await started;
    child.exitCode = 3;
    child.emit("exit", 3, null);
    child.emit("close", 3, null);

    assert.deepEqual(await owned.stop(), {
      outcome: "already-exited",
      code: 3,
      signal: null,
      processError: false,
    });
  });
});

test("a child error or synchronous signal error cannot be reported as graceful", async (t) => {
  await t.test("asynchronous child error followed by SIGKILL", async () => {
    const child = makeChild();
    child.kill = (signal) => {
      child.kills.push(signal);
      if (signal === "SIGINT") process.nextTick(() => child.emit("error", new Error("signal failed")));
      if (signal === "SIGKILL") {
        child.signalCode = "SIGKILL";
        child.emit("exit", null, "SIGKILL");
        child.emit("close", null, "SIGKILL");
      }
      return false;
    };
    const owned = createOwnedCli({ spawn: () => child, stopTimeoutMs: 5, forceStopTimeoutMs: 50 });
    const started = owned.start();
    child.stdout.write(`${url}\n`);
    await started;

    assert.deepEqual(await owned.stop(), {
      outcome: "forced",
      code: null,
      signal: "SIGKILL",
      processError: true,
    });
    assert.deepEqual(child.kills, ["SIGINT", "SIGKILL"]);
  });

  await t.test("synchronous kill error followed by SIGKILL", async () => {
    const child = makeChild();
    child.kill = (signal) => {
      child.kills.push(signal);
      if (signal === "SIGINT") throw new Error("signal failed");
      if (signal === "SIGKILL") {
        child.signalCode = "SIGKILL";
        child.emit("exit", null, "SIGKILL");
        child.emit("close", null, "SIGKILL");
      }
      return true;
    };
    const owned = createOwnedCli({ spawn: () => child, stopTimeoutMs: 5, forceStopTimeoutMs: 50 });
    const started = owned.start();
    child.stdout.write(`${url}\n`);
    await started;

    assert.deepEqual(await owned.stop(), {
      outcome: "forced",
      code: null,
      signal: "SIGKILL",
      processError: true,
    });
    assert.deepEqual(child.kills, ["SIGINT", "SIGKILL"]);
  });
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

  assert.deepEqual(await owned.stop(), {
    outcome: "forced",
    code: null,
    signal: "SIGKILL",
    processError: true,
  });
  assert.deepEqual(child.kills, ["SIGINT", "SIGKILL"]);
  assert.equal(child.listenerCount("error"), 0);
});

test("can retry shutdown after a child did not close following the forced signal", async () => {
  const child = makeChild();
  child.kill = (signal) => {
    child.kills.push(signal);
    if (signal === "SIGINT" && child.kills.filter((entry) => entry === "SIGINT").length > 1) {
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
  assert.deepEqual(await owned.stop(), {
    outcome: "abnormal",
    code: null,
    signal: "SIGINT",
    processError: false,
  });
  assert.deepEqual(child.kills, ["SIGINT", "SIGKILL", "SIGINT"]);
});
