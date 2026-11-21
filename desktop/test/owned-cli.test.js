import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, chmodSync, symlinkSync, linkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import test from "node:test";
import { ATTACH_ARGUMENTS, acquireDesktopGate, createOwnedCli, installedCli } from "../src/owned-cli.js";

const url = "http://127.0.0.1:43817/#token=abcdefghijklmnopqrstuvwxyz0123456789";

function makeChild() {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stdin = new PassThrough();
  child.received = "";
  child.stdin.on("data", (chunk) => { child.received += chunk.toString("utf8"); });
  child.exitCode = null;
  child.signalCode = null;
  child.kills = [];
  child.kill = (signal) => { child.kills.push(signal); return true; };
  child.finish = (output, code = 0, signal = null) => {
    if (output) child.stdout.write(output);
    child.exitCode = code;
    child.signalCode = signal;
    setImmediate(() => child.emit("close", code, signal));
  };
  return child;
}

test("attaches with the fixed attach arguments and never spawns `ui serve`", async () => {
  const child = makeChild();
  let spawned;
  const owned = createOwnedCli({ spawn: (...args) => { spawned = args; return child; } });
  const started = owned.start();
  assert.deepEqual(spawned, ["hz", ["ui", "attach", "--bridge-token-stdin"], {
    shell: false,
    windowsHide: true,
    stdio: ["pipe", "pipe", "ignore"],
  }]);
  assert.deepEqual([...ATTACH_ARGUMENTS], spawned[1]);
  assert.equal(spawned[1].includes("serve"), false);
  assert.equal(spawned[1].includes("--port"), false);
  child.finish(`${url}\n`);
  const connection = await started;
  assert.equal(connection.origin, "http://127.0.0.1:43817");
  assert.equal(connection.token, "abcdefghijklmnopqrstuvwxyz0123456789");
  assert.equal(owned.currentConnection(), connection);
  assert.equal(JSON.stringify(connection).includes(url), false);
  assert.equal(owned.isRunning(), true);
});

test("the bridge token goes to attach on stdin only, never in arguments or this process's environment", async () => {
  const child = makeChild();
  let spawned;
  const token = "b".repeat(43);
  const owned = createOwnedCli({ spawn: (...args) => { spawned = args; return child; }, bridgeToken: token, env: { HZ_CALLER: "desktop-app" } });
  const started = owned.start();
  child.finish(`${url}\n`);
  await started;
  assert.equal(child.received, `${token}\n`);
  assert.equal(spawned[1].join(" ").includes(token), false);
  assert.equal(Object.values(spawned[2].env).includes(token), false);
  assert.equal(spawned[2].env.HZ_CALLER, "desktop-app");
  assert.equal(process.env.HZ_CALLER, undefined);
});

test("one attach per handle: a second start reuses it and spawns nothing", async () => {
  const child = makeChild();
  let spawns = 0;
  const owned = createOwnedCli({ spawn: () => { spawns += 1; return child; } });
  const first = owned.start();
  child.finish(`${url}\n`);
  assert.equal(await owned.start(), await first);
  assert.equal(spawns, 1);
});

test("a failed attach rejects with one reason, exposes no credential and never falls back to serving", async (t) => {
  for (const [name, output, code] of [
    ["nonzero exit", `${url}\n`, 1],
    ["no line", "", 0],
    ["two lines", `${url}\nmore\n`, 0],
    ["unfinished line", url, 0],
    ["oversized", "x".repeat(4097), 0],
    ["not a link", "x\n", 0],
  ]) {
    await t.test(name, async () => {
      const child = makeChild();
      const spawned = [];
      const owned = createOwnedCli({ spawn: (_command, args) => { spawned.push(args); return child; } });
      const started = owned.start();
      child.finish(output, code);
      await assert.rejects(started, (error) => !error.message.includes("abcdefghijklmnopqrstuvwxyz"));
      assert.deepEqual(spawned, [[...ATTACH_ARGUMENTS]]);
      assert.equal(owned.isRunning(), false);
      assert.deepEqual(await owned.exited(), { code: null, signal: null, failedToStart: true });
      assert.deepEqual(await owned.stop(), { outcome: "not-started", code: null, signal: null, processError: false });
    });
  }
});

test("a slow attach times out and only the attach command gets SIGINT", async () => {
  const child = makeChild();
  const owned = createOwnedCli({ spawn: () => child, startupTimeoutMs: 5 });
  await assert.rejects(owned.start(), /took too long/);
  assert.deepEqual(child.kills, ["SIGINT"]);
});

test("a spawn error rejects", async () => {
  const child = makeChild();
  const owned = createOwnedCli({ spawn: () => child });
  const started = owned.start();
  child.emit("error", new Error("spawn failed"));
  await assert.rejects(started);
  await assert.rejects(createOwnedCli({ spawn: () => { throw new Error("no"); } }).start());
});

test("quitting detaches and signals nothing: the owned service is not the app's to stop", async () => {
  const child = makeChild();
  const owned = createOwnedCli({ spawn: () => child });
  assert.deepEqual(await owned.stop(), { outcome: "not-started", code: null, signal: null, processError: false });
  const started = owned.start();
  child.finish(`${url}\n`);
  await started;
  assert.deepEqual(await owned.stop(), { outcome: "detached", code: null, signal: null, processError: false });
  assert.deepEqual(child.kills, []);
  assert.equal(owned.isRunning(), false);
});

test("installed binding selects only a configured absolute executable", (t) => {
  const home = mkdtempSync(join(tmpdir(), "hz-binding-"));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const directory = join(home, ".config", "heinzel");
  mkdirSync(directory, { recursive: true });
  const receipt = join(directory, "desktop-owned-cli.json");
  const executable = join(home, "hz");
  writeFileSync(executable, "#!/bin/sh\n", { mode: 0o700 });
  const bind = (value) => writeFileSync(receipt, JSON.stringify(value), { mode: 0o600 });
  assert.throws(() => installedCli(home), /could not find/);
  bind({ version: 1, executable });
  assert.equal(installedCli(home), executable);
  for (const value of [
    { version: 1, executable: "hz" }, { version: 2, executable },
    { version: 1, executable, extra: true }, { version: 1, executable: home },
    { version: 1, executable: join(home, "missing") },
  ]) {
    bind(value);
    assert.throws(() => installedCli(home), /could not find/);
  }
  bind({ version: 1, executable });
  chmodSync(receipt, 0o644);
  assert.throws(() => installedCli(home), /could not find/);
  chmodSync(receipt, 0o600);
  const secondLink = join(home, "other-receipt");
  linkSync(receipt, secondLink);
  assert.throws(() => installedCli(home), /could not find/);
  rmSync(secondLink);
  chmodSync(directory, 0o777);
  assert.throws(() => installedCli(home), /could not find/);
  chmodSync(directory, 0o700);
  assert.equal(installedCli(home), executable);
  chmodSync(executable, 0o600);
  assert.throws(() => installedCli(home), /could not find/);
  rmSync(receipt);
  symlinkSync(executable, receipt);
  assert.throws(() => installedCli(home), /could not find/);
});

test("an older hz without `ui attach` gets a clear update message", async () => {
  const child = makeChild();
  const owned = createOwnedCli({ spawn: () => child });
  const started = owned.start();
  child.finish("", 2);
  await assert.rejects(started, /hz system update/);
});

test("a service that stops answering (a crash restart: new token, bridge forgotten) settles exited for a re-attach", async () => {
  const child = makeChild();
  let tick;
  let cleared = 0;
  let answering = true;
  const probed = [];
  const owned = createOwnedCli({
    spawn: () => child,
    probe: async (connection) => { probed.push(connection.origin); if (!answering) throw Object.assign(new Error("401"), { status: 401 }); },
    setInterval: (callback) => { tick = callback; return 7; },
    clearInterval: (id) => { assert.equal(id, 7); cleared += 1; },
  });
  const started = owned.start();
  child.finish(`${url}\n`);
  await started;
  await tick();
  assert.equal(owned.isRunning(), true);
  answering = false;
  await tick();
  assert.equal(owned.isRunning(), false);
  assert.deepEqual(await owned.exited(), { code: null, signal: null, serviceLost: true });
  assert.equal(cleared, 1);
  assert.deepEqual(probed, ["http://127.0.0.1:43817", "http://127.0.0.1:43817"]);
  // The next attach is a new handle, so it registers the bridge token again with the new server.
  const next = makeChild();
  const again = createOwnedCli({ spawn: () => next, bridgeToken: "c".repeat(43) });
  const reattached = again.start();
  next.finish(`${url}\n`);
  await reattached;
  assert.equal(next.received, `${"c".repeat(43)}\n`);
});

test("quitting stops the probe", async () => {
  const child = makeChild();
  let cleared = 0;
  const owned = createOwnedCli({ spawn: () => child, probe: async () => {}, setInterval: () => 9, clearInterval: () => { cleared += 1; } });
  const started = owned.start();
  child.finish(`${url}\n`);
  await started;
  await owned.stop();
  assert.equal(cleared, 1);
});

test("uses the resolved executable without shell lookup", async () => {
  const child = makeChild();
  let command;
  const owned = createOwnedCli({ executable: () => "/owned/hz", spawn: (value) => { command = value; return child; } });
  const started = owned.start();
  child.finish(`${url}\n`);
  await started;
  assert.equal(command, "/owned/hz");
  await owned.stop();
});

test("desktop gate holds the installer lock and refuses a pending journal", { skip: process.platform !== "darwin" }, async (t) => {
  const home = mkdtempSync(join(tmpdir(), "hz-gate-"));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const directory = join(home, ".config", "heinzel");
  mkdirSync(directory, { recursive: true });
  const lock = join(directory, "desktop-use.lock");
  writeFileSync(lock, "", { mode: 0o600 });
  const gate = await acquireDesktopGate(home);
  const { spawnSync } = await import("node:child_process");
  const probe = () => spawnSync("/usr/bin/python3", ["-c", "import fcntl,sys; f=open(sys.argv[1]); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)", lock]);
  assert.notEqual(probe().status, 0);
  await assert.rejects(acquireDesktopGate(home), /could not secure/);
  const closed = new Promise((resolve) => gate.once("close", resolve));
  gate.stdin.end();
  await closed;
  assert.equal(probe().status, 0);
  chmodSync(lock, 0o644);
  await assert.rejects(acquireDesktopGate(home), /could not secure/);
  chmodSync(lock, 0o600);
  writeFileSync(join(directory, "desktop-installation-journal.json"), "{}", { mode: 0o600 });
  await assert.rejects(acquireDesktopGate(home), /could not secure/);
});
