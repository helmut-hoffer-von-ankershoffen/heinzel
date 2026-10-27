import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { createInstanceLifecycle } from "../src/instance-lifecycle.js";

function setup({ locked = true, parseConnection = null, createWindow = null } = {}) {
  const app = new EventEmitter();
  app.isPackaged = true;
  app.requestSingleInstanceLock = () => locked;
  app.quit = () => { app.didQuit = true; };
  const events = [];
  const connection = { origin: "http://127.0.0.1:8737", token: "a".repeat(32) };
  const lifecycle = createInstanceLifecycle({
    app,
    parseConnection: parseConnection ?? ((args) => {
      events.push("parse");
      if (args[0] !== "trusted-connection") throw new Error("bad connection");
      return connection;
    }),
    createWindow: createWindow ?? (async () => {
      events.push("window");
      await Promise.resolve();
      return Object.assign(new EventEmitter(), {
        isDestroyed: () => false,
        isMinimized: () => false,
        focus() { events.push("focus"); },
        isVisible: () => true,
      });
    }),
    focusWindow: (window) => window.focus(),
  });
  return { app, events, connection, lifecycle };
}

test("single-instance lock is acquired before connection parsing and window creation", async () => {
  const { events, lifecycle } = setup();
  assert.equal(lifecycle.acquire(["trusted-connection"]), true);
  assert.deepEqual(events, ["parse"]);
  lifecycle.setReady();
  await lifecycle.open();
  assert.deepEqual(events, ["parse", "window"]);
});

test("a second launch focuses the existing window and cannot replace its token", async () => {
  const { app, connection, lifecycle, events } = setup();
  assert.equal(lifecycle.acquire(["trusted-connection"]), true);
  lifecycle.setReady();
  const window = await lifecycle.open();
  app.emit("second-instance", {}, ["malicious-url-and-token"]);
  assert.equal(lifecycle.currentConnection(), connection);
  assert.equal(await lifecycle.open(), window);
  assert.equal(events.filter((event) => event === "window").length, 1);
  assert.ok(events.filter((event) => event === "focus").length >= 1);
});

test("a process without the Electron single-instance lock never parses arguments or creates a window", async () => {
  let creates = 0;
  const { app, events, lifecycle } = setup({ locked: false, createWindow: () => { creates += 1; } });
  assert.equal(lifecycle.acquire(["trusted-connection"]), false);
  await assert.rejects(lifecycle.open(), /verified local Heinzelmännchen connection/);
  assert.equal(app.didQuit, true);
  assert.equal(creates, 0);
  assert.deepEqual(events, []);
});

test("invalid initial connection quits without opening a window", () => {
  const { app, events, lifecycle } = setup();
  assert.throws(() => lifecycle.acquire(["untrusted"]), /bad connection/);
  assert.equal(app.didQuit, true);
  assert.deepEqual(events, ["parse"]);
});

test("parallel open requests share one pending window creation", async () => {
  const { lifecycle, events } = setup();
  lifecycle.acquire(["trusted-connection"]);
  lifecycle.setReady();
  const [first, second] = await Promise.all([lifecycle.open(), lifecycle.open()]);
  assert.equal(first, second);
  assert.equal(events.filter((event) => event === "window").length, 1);
});

test("duplicate launch arguments are never passed to connection parsing", () => {
  const parsed = [];
  const { app, lifecycle } = setup({ parseConnection: (args) => { parsed.push(args); return { token: "original" }; } });
  lifecycle.acquire(["trusted-connection"]);
  app.emit("second-instance", {}, ["https://attacker.invalid/#token=evil"]);
  assert.deepEqual(parsed, [["trusted-connection"]]);
  assert.equal(lifecycle.currentConnection().token, "original");
});

test("second instance before readiness opens one window after readiness", async () => {
  const { app, events, lifecycle } = setup();
  lifecycle.acquire(["trusted-connection"]);
  app.emit("second-instance", {}, ["malicious"]);
  assert.deepEqual(events, ["parse"]);
  lifecycle.setReady();
  await lifecycle.open();
  assert.deepEqual(events, ["parse", "window", "focus"]);
});

test("an early second instance cannot leave a rejected window creation unhandled", async () => {
  const failure = new Error("window creation failed");
  const unhandled = [];
  const onUnhandled = (reason) => unhandled.push(reason);
  const { app, events, lifecycle } = setup({
    createWindow: () => {
      events.push("window");
      return Promise.reject(failure);
    },
  });
  process.on("unhandledRejection", onUnhandled);
  try {
    lifecycle.acquire(["trusted-connection"]);
    app.emit("second-instance", {}, ["malicious"]);
    lifecycle.setReady();
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(events, ["parse"]);
    assert.deepEqual(unhandled, []);

    await assert.rejects(lifecycle.open(), failure);
    assert.deepEqual(events, ["parse", "window"]);
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(unhandled, []);
  } finally {
    process.off("unhandledRejection", onUnhandled);
  }
});

test("second instance during pending creation focuses the created window", async () => {
  let finishCreate;
  const { app, events, lifecycle } = setup({
    createWindow: () => new Promise((resolve) => {
      events.push("window");
      finishCreate = () => resolve(Object.assign(new EventEmitter(), {
        isDestroyed: () => false,
        isMinimized: () => false,
        focus() { events.push("focus"); },
      }));
    }),
  });
  lifecycle.acquire(["trusted-connection"]);
  lifecycle.setReady();
  const opening = lifecycle.open();
  app.emit("second-instance", {}, ["malicious"]);
  finishCreate();
  await opening;
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(events.filter((event) => event === "window").length, 1);
  assert.ok(events.includes("focus"));
});
