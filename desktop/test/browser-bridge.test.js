import assert from "node:assert/strict";
import test from "node:test";
import { BridgeError } from "../src/browser-agent.js";
import { BRIDGE_PATH, createBrowserBridge } from "../src/browser-bridge.js";

const AGENT = { id: "chat-1", name: "Ada", kind: "heinzel" };
const TOKEN = "s".repeat(40);

function fixture({ handle = async () => ({}), connection = () => ({ origin: "http://127.0.0.1:8737", token: TOKEN }) } = {}) {
  const sockets = [], timers = [], handled = [];
  let listener = null;
  const bridge = createBrowserBridge({
    connection,
    engine: { handle: (call) => { handled.push(call); return handle(call); } },
    listen: (added) => { listener = added; return () => { listener = null; }; },
    open: (url, origin) => {
      const handlers = {};
      const socket = { url, origin, readyState: 0, sent: [], closed: null,
        addEventListener: (name, handler) => { handlers[name] = handler; },
        send: (data) => socket.sent.push(JSON.parse(data)),
        close: (code, reason) => { socket.closed = [code, reason]; socket.readyState = 3; },
        fire: (name, event = {}) => handlers[name]?.(event),
        open: () => { socket.readyState = 1; socket.fire("open"); },
        say: (value) => socket.fire("message", { data: typeof value === "string" ? value : JSON.stringify(value) }) };
      sockets.push(socket); return socket;
    },
    setTimer: (run, delay) => { const timer = { run, delay }; timers.push(timer); return timer; },
    clearTimer: (timer) => { timers.splice(timers.indexOf(timer), 1); },
  });
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  const ready = () => { bridge.start(); const socket = sockets.at(-1); socket.open(); socket.say({ ready: true, version: 1 }); socket.sent.length = 0; return socket; };
  return { bridge, sockets, timers, handled, settle, ready, emit: (event) => listener?.(event) };
}

test("the bridge dials the local web head and proves itself with the session token first", () => {
  const f = fixture();
  f.bridge.start();
  f.bridge.start();
  assert.equal(f.sockets.length, 1);
  const [socket] = f.sockets;
  assert.equal(socket.url, `ws://127.0.0.1:8737${BRIDGE_PATH}`);
  assert.equal(socket.origin, "http://127.0.0.1:8737");
  assert.deepEqual(socket.sent, []);
  socket.open();
  assert.deepEqual(socket.sent, [{ hello: "hz-browser-bridge", version: 1, token: TOKEN }]);
  assert.equal(f.bridge.connected(), false);
});

test("nothing runs before the daemon's exact ready answer", async () => {
  for (const first of [{ id: "1", command: "tabs", agent: AGENT, args: {} }, { ready: true }, { ready: true, version: 2 },
    { ready: "yes", version: 1 }, { ready: true, version: 1, extra: 1 }, [], "not json"]) {
    const f = fixture();
    f.bridge.start();
    const [socket] = f.sockets;
    socket.open();
    socket.say(first);
    await f.settle();
    assert.notEqual(socket.closed, null);
    assert.deepEqual(f.handled, []);
    assert.equal(f.bridge.connected(), false);
  }
});

test("a command is answered under its id; a refusal carries its code", async () => {
  const f = fixture({ handle: async ({ command }) => {
    if (command === "read") throw new BridgeError("paused", "The person took over this tab.");
    if (command === "text") throw new TypeError("internal detail");
    return { tabs: [] };
  } });
  const socket = f.ready();
  assert.equal(f.bridge.connected(), true);
  socket.say({ id: "a", command: "tabs", agent: AGENT, args: {} });
  socket.say({ id: "b", command: "read", agent: AGENT, args: { tab: "t-00000000" } });
  socket.say({ id: "c", command: "text", agent: AGENT, args: { tab: "t-00000000" } });
  await f.settle();
  assert.deepEqual(f.handled[0], { command: "tabs", agent: AGENT, args: {} });
  assert.deepEqual(socket.sent, [
    { id: "a", ok: true, result: { tabs: [] } },
    { id: "b", ok: false, error: { code: "paused", message: "The person took over this tab." } },
    { id: "c", ok: false, error: { code: "failed", message: "The browser could not do that." } },
  ]);
});

test("exact keys: a malformed command never reaches the engine", async () => {
  const f = fixture();
  const socket = f.ready();
  for (const message of [
    { id: "1", command: "tabs", agent: AGENT },
    { id: "2", command: "tabs", agent: AGENT, args: {}, window: 1 },
    { id: "3", command: "evaluate", agent: AGENT, args: {} },
    { id: "4", command: "tabs", agent: "chat-1", args: {} },
    { id: "5", command: "tabs", agent: AGENT, args: [] },
    { id: "6", command: "constructor", agent: AGENT, args: {} },
    { id: "7", command: 7, agent: AGENT, args: {} },
  ]) socket.say(message);
  await f.settle();
  assert.deepEqual(f.handled, []);
  assert.deepEqual(socket.sent.map((answer) => [answer.id, answer.ok, answer.error.code]), ["1", "2", "3", "4", "5", "6", "7"].map((id) => [id, false, "invalid"]));
  assert.equal(socket.closed, null);
  // Without an id, with a binary frame or with too much there is nothing to answer: the socket goes.
  for (const bad of [{ command: "tabs", agent: AGENT, args: {} }, { id: "x".repeat(65), command: "tabs", agent: AGENT, args: {} }]) {
    const next = fixture();
    const target = next.ready();
    target.say(bad);
    assert.notEqual(target.closed, null);
    assert.deepEqual(next.handled, []);
  }
  const big = fixture();
  const target = big.ready();
  target.say(JSON.stringify({ id: "1", command: "tabs", agent: AGENT, args: { pad: "x".repeat(2 * 1024 * 1024) } }));
  target.fire("message", { data: new ArrayBuffer(4) });
  assert.equal(target.closed[0], 1009);
  assert.deepEqual(big.handled, []);
});

test("tab events go to the daemon only while the bridge is ready", () => {
  const f = fixture();
  f.bridge.start();
  const [socket] = f.sockets;
  socket.open();
  f.emit({ event: "stop", agent: "chat-1", tab: null });
  assert.equal(socket.sent.length, 1);
  socket.say({ ready: true, version: 1 });
  f.emit({ event: "take-over", agent: "chat-1", tab: "t-00000001" });
  assert.deepEqual(socket.sent.at(-1), { event: "take-over", agent: "chat-1", tab: "t-00000001" });
});

test("a lost socket is dialled again with a growing wait; stop ends it", async () => {
  const f = fixture();
  const socket = f.ready();
  socket.readyState = 3;
  socket.fire("close");
  assert.equal(f.bridge.connected(), false);
  assert.deepEqual(f.timers.map((timer) => timer.delay), [1000]);
  f.timers.shift().run();
  assert.equal(f.sockets.length, 2);
  // A refused dial: error and close both arrive, one retry is planned, and the wait doubles.
  f.sockets[1].fire("error");
  f.sockets[1].fire("close");
  assert.deepEqual(f.timers.map((timer) => timer.delay), [2000]);
  f.timers.shift().run();
  // An answer of a call that outlived its socket goes nowhere.
  let finish;
  const slow = fixture({ handle: () => new Promise((resolve) => { finish = resolve; }) });
  const old = slow.ready();
  old.say({ id: "1", command: "tabs", agent: AGENT, args: {} });
  old.readyState = 3;
  old.fire("close");
  slow.timers.shift().run();
  const fresh = slow.sockets[1];
  fresh.open(); fresh.say({ ready: true, version: 1 });
  finish({});
  await slow.settle();
  assert.deepEqual(old.sent, []);
  assert.deepEqual(fresh.sent, [{ hello: "hz-browser-bridge", version: 1, token: TOKEN }]);
  // Stop closes the socket, forgets the retry and the listener.
  f.sockets[2].open();
  f.bridge.stop();
  assert.deepEqual(f.sockets[2].closed, [1000, "Closing."]);
  f.sockets[2].fire("close");
  assert.deepEqual(f.timers, []);
  f.emit({ event: "stop", agent: "chat-1", tab: null });
  assert.equal(f.sockets.length, 3);
});

test("without a local service there is no dial, only a later try", () => {
  let current = null;
  const f = fixture({ connection: () => current });
  f.bridge.start();
  assert.equal(f.sockets.length, 0);
  current = { origin: "http://127.0.0.1:9000", token: TOKEN };
  f.timers.shift().run();
  assert.equal(f.sockets[0].url, "ws://127.0.0.1:9000/api/browser-bridge");
});
