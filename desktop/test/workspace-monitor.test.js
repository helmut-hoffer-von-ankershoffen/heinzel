import assert from "node:assert/strict";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { createWorkspaceMonitor } from "../src/workspace-monitor.js";
import { readWorkspaceFacts, versionFacts, watchWorkspaceAuthority } from "../src/workspace-facts.js";

const target = { remote: true, origin: "https://studio.example" };
const settle = () => new Promise(setImmediate);
function fixture() {
  let timer = null, calls = 0, losses = 0, returns = 0;
  let probe = async () => { throw new Error("offline"); };
  const monitor = createWorkspaceMonitor({ probe: (...args) => { calls++; return probe(...args); },
    changed() {}, lost() { losses++; }, recovered() { returns++; },
    schedule: (callback, delay) => { assert.equal(timer, null); timer = { callback, delay }; return timer; },
    unschedule: () => { timer = null; }, random: () => 0.5 });
  monitor.select(target);
  return { monitor, counts: () => [calls, losses, returns], change: (next) => { probe = next; },
    async tick(expected) { assert.equal(timer?.delay, expected); const next = timer; timer = null; next.callback(); await settle(); },
    idle: () => assert.equal(timer, null),
    async lose() { for (let i = 0; i < 3; i++) await this.tick(5000); } };
}
test("confirmation opens once before the immediate retry; transient retries plateau at 40s while enabled", async () => {
  const f = fixture();
  await f.tick(5000); await f.tick(5000); assert.equal(f.monitor.state(), null);
  await f.tick(5000); assert.deepEqual(f.counts(), [3, 1, 0]);
  for (const delay of [0, 5000, 10000, 20000, 40000, 40000]) await f.tick(delay);
  assert.deepEqual(f.counts(), [9, 1, 0]);
});
test("no route suspends requests; explicit pause survives path restoration and sleep", async () => {
  const f = fixture(); f.change(async () => { throw Object.assign(new Error(), { code: "ENETUNREACH" }); });
  await f.lose(); assert.equal(f.monitor.state().phase, "waiting"); f.idle();
  f.monitor.pause(target.origin); f.change(async () => ({}));
  f.monitor.awake(false); f.monitor.wake(); f.monitor.awake(true); f.monitor.wake();
  assert.deepEqual(f.counts(), [3, 1, 0]); f.idle();
  f.monitor.resume(target.origin); await settle();
  assert.deepEqual(f.counts(), [4, 1, 1]); assert.equal(f.monitor.state(), null);
});
for (const choice of ["pause", "selection", "disconnect", "edit"]) {
  test("late authenticated success cannot undo " + choice, async () => {
    const f = fixture(); await f.lose();
    let finish, signal;
    f.change((_target, cancellation) => { signal = cancellation; return new Promise((resolve) => { finish = resolve; }); });
    await f.tick(0);
    if (choice === "pause") f.monitor.pause(target.origin);
    if (choice === "selection") f.monitor.select({ ...target, origin: "https://other.example" });
    if (choice === "disconnect") f.monitor.select(null);
    if (choice === "edit") f.monitor.hold(true);
    assert.equal(signal.aborted, true);
    finish({}); await settle(); assert.deepEqual(f.counts(), [4, 1, 0]);
    if (choice === "edit") {
      f.change(async () => ({})); f.monitor.hold(false); await settle(); assert.deepEqual(f.counts(), [5, 1, 1]);
    }
  });
}
for (const [status, phase] of [[401, "signin"], [403, "denied"], [404, "address"], [503, "unavailable"]]) {
  test("authenticated lifecycle status " + status + " has its own recovery policy", async () => {
    const f = fixture(); f.change(async () => { throw { status }; });
    if (status === 503) await f.lose(); else await f.tick(5000);
    assert.equal(f.monitor.state().phase, phase);
    if (status === 503) await f.tick(0);
    else { f.idle(); f.monitor.wake(); await settle(); assert.deepEqual(f.counts(), [1, 1, 0]); }
  });
}
test("version facts distinguish client socket, backend socket, uptime and installed Core", () => {
  const web = { version: "0.9.58", backend_ip: "2001:db8::8", running_since: "2026-01-02T03:04:00Z" };
  const read = (installed) => versionFacts({ web, installed, observed_at: "2026-01-02T03:04:14Z" }, "127.0.0.1");
  assert.deepEqual(read({ version: "0.9.59" }), { version: "0.9.58", installedVersion: "0.9.59",
    ipAddress: "2001:db8::8", clientAddress: "127.0.0.1", uptimeSeconds: 14 });
  assert.equal(read({ version: "0.9.58" }).installedVersion, null);
  assert.equal(read({}).installedVersion, null);
  assert.equal(versionFacts({ web: { backend_ip: "proxy.example" } }, null).ipAddress, null);
  assert.equal(versionFacts({}, null).uptimeSeconds, null);
});

test("a credential changing during the request cannot confirm loss or restore the old session", async () => {
  const f = fixture();
  f.change(async () => { throw Object.assign(new Error(), { code: "SESSION_CHANGED" }); });
  for (let i = 0; i < 4; i++) await f.tick(5000);
  assert.deepEqual(f.counts(), [4, 0, 0]);
  assert.equal(f.monitor.state(), null);
});

test("Information reads the document credential and drops a response after navigation or authority changes", async () => {
  const frame = { url: target.origin + "/chat" };
  let credential = "paired", duringFetch = () => {};
  const contents = {
    mainFrame: frame, isDestroyed: () => false,
    executeJavaScriptInIsolatedWorld: async () => ({ launch: null, device: credential }),
    session: {
      resolveProxy: async () => "PROXY fixture",
      fetch: async (url, options) => {
        assert.equal(url, target.origin + "/api/workspace-version");
        assert.equal(options.headers.Authorization, "Bearer paired");
        duringFetch();
        return Response.json({ web: { version: "0.9.58", backend_ip: "::1" } });
      },
    },
  };
  const read = () => readWorkspaceFacts({ ...target, token: "expired" }, contents, AbortSignal.timeout(1000));
  assert.equal((await read()).ipAddress, "::1");
  duringFetch = () => { frame.url = target.origin + "/settings"; };
  assert.deepEqual(await read(), {});
  duringFetch = () => { credential = "changed"; };
  assert.deepEqual(await read(), {});
  frame.url = "https://different.example/chat";
  assert.deepEqual(await read(), {});
});

test("open Information refreshes once per observed authority and ignores a read completed after close", async () => {
  const pending = [], observed = [], controller = new AbortController();
  const signals = ["first", "first", "second", null];
  const finished = Promise.withResolvers(), held = Promise.withResolvers();
  let reads = 0;
  const contents = {
    isDestroyed: () => false,
    executeJavaScriptInIsolatedWorld: async () => {
      reads++;
      if (signals.length) {
        const value = signals.shift();
        pending.push(value);
        return value;
      }
      finished.resolve();
      return held.promise;
    },
  };
  watchWorkspaceAuthority(target, contents, controller.signal, () => observed.push(pending.at(-1)), 1);
  await finished.promise;
  controller.abort();
  held.resolve("stale");
  await settle();
  assert.deepEqual(observed, ["first", "second", null]);
  assert.equal(reads, 5);
});

test("Information falls back once on a 401 while the rejected launch token remains in the document", async () => {
  for (const [status, accepted] of [[401, true], [403, false], [404, false], [503, false]]) {
    const authority = { launch: "stale", device: "paired" }, sent = [];
    const contents = {
      mainFrame: { url: target.origin + "/chat" }, isDestroyed: () => false,
      executeJavaScriptInIsolatedWorld: async () => ({ ...authority }),
      session: {
        resolveProxy: async () => "PROXY fixture",
        fetch: async (_url, options) => {
          assert.equal(options.method ?? "GET", "GET");
          sent.push(options.headers.Authorization);
          return sent.length === 1 ? new Response(null, {status}) : Response.json({ web: { version: "0.9.58" } });
        },
      },
    };
    const facts = await readWorkspaceFacts(target, contents, AbortSignal.timeout(1000));
    assert.deepEqual(sent, accepted ? ["Bearer stale", "Bearer paired"] : ["Bearer stale"]);
    assert.equal(facts.version, accepted ? "0.9.58" : undefined);
    assert.deepEqual(authority, { launch: "stale", device: "paired" });
  }
});

test("Information discards a change to either credential or the route between or during fallback reads", async () => {
  for (const changeAt of [1, 2]) {
    for (const change of ["launch", "device", "route", "cancel"]) {
      const authority = { launch: "stale", device: "paired" }, frame = { url: target.origin + "/chat" };
      const controller = new AbortController();
      let reads = 0;
      const contents = {
        mainFrame: frame, isDestroyed: () => false,
        executeJavaScriptInIsolatedWorld: async () => ({ ...authority }),
        session: {
          resolveProxy: async () => "PROXY fixture",
          fetch: async () => {
            if (++reads === changeAt) {
              if (change === "route") frame.url = target.origin + "/settings";
              else if (change === "cancel") controller.abort();
              else authority[change] = "changed";
            }
            return reads === 1 ? new Response(null, {status:401}) : Response.json({web:{version:"0.9.58"}});
          },
        },
      };
      assert.deepEqual(await readWorkspaceFacts(target, contents, controller.signal), {});
      assert.equal(reads, changeAt);
    }
  }
});

test("an open Information view observes device credential rotation even with the same stored launch credential", { timeout: 2000 }, async (t) => {
  const controller = new AbortController(), changed = [], finished = Promise.withResolvers();
  t.after(() => controller.abort());
  let device = "device-A";
  const storage = {
    location: { origin: target.origin }, crypto, TextEncoder,
    sessionStorage: { getItem: key => key === "hz.token" ? "stale" : null },
    localStorage: { getItem: key => key === "hz.device" ? device : null },
  };
  const contents = { isDestroyed: () => false,
    executeJavaScriptInIsolatedWorld: (_world, [{ code }]) => runInNewContext(code, storage) };
  watchWorkspaceAuthority(target, contents, controller.signal, () => {
    changed.push(device);
    if (device === "device-A") device = "device-B";
    else { controller.abort(); finished.resolve(); }
  }, 1);
  await finished.promise;
  assert.deepEqual(changed, ["device-A", "device-B"]);
});
