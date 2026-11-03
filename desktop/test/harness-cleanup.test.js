import assert from "node:assert/strict";
import test from "node:test";
import { cleanupHarness } from "../e2e/harness-cleanup.mjs";

function fixture({ close, alive = false, pid = 91 }) {
  const calls = [];
  const child = { exitCode: null, signalCode: null, kill: (signal) => calls.push(signal) };
  const app = { close: async () => { await close(); child.exitCode = 0; }, process: () => child };
  const options = {
    app, electronProcess: child, stopped: false, root: "/temporary-test-root", ownedPid: pid,
    loadPid: async () => { throw new Error("missing"); },
    processExists: () => alive,
    remove: async (path) => { calls.push(`removed:${path}`); },
    timeoutMs: 5,
  };
  return { calls, options };
}

test("stuck Electron teardown is bounded and retains evidence without signaling an unknown child", async () => {
  const { calls, options } = fixture({ close: () => new Promise(() => {}), alive: true });
  await assert.rejects(cleanupHarness(options), /evidence was retained/);
  assert.deepEqual(calls, ["SIGTERM"]); // only the test-owned Electron ChildProcess
});

test("a surviving owned child or unknown identity retains the temporary profile", async () => {
  for (const settings of [{ alive: true, pid: 91 }, { alive: false, pid: null }, { alive: false, pid: Number.NaN }]) {
    const { calls, options } = fixture({ close: async () => {}, ...settings });
    await assert.rejects(cleanupHarness(options), /evidence was retained/);
    assert.deepEqual(calls, []);
  }
});

test("cleanly closed controller and child allow temporary profile cleanup", async () => {
  const { calls, options } = fixture({ close: async () => {} });
  await cleanupHarness(options);
  assert.deepEqual(calls, ["removed:/temporary-test-root"]);
});

test("packaged smoke refuses locally without explicit disposable CI authorization", async () => {
  const { spawnSync } = await import("node:child_process");
  const { fileURLToPath } = await import("node:url");
  const script = fileURLToPath(new URL("../e2e/native-startup.mjs", import.meta.url));
  for (const env of [
    { HEINZEL_ELECTRON_EXECUTABLE: "/not-launched", CI: "false", HZ_DISPOSABLE_NATIVE_CI: "1" },
    { HEINZEL_ELECTRON_EXECUTABLE: "/not-launched", CI: "true" },
  ]) {
    const result = spawnSync(process.execPath, [script], { env, encoding: "utf8", timeout: 5000 });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /explicitly disposable CI/);
  }
});

test("a rejected launch retains the profile when no controller identity is available", async () => {
  const calls = [];
  await assert.rejects(cleanupHarness({
    app: undefined, electronProcess: undefined, stopped: false, launchAttempted: true,
    root: "/temporary-test-root", processExists: () => { throw new Error("no PID may be queried"); },
    remove: async () => { calls.push("removed"); },
  }), /launch did not return a controller.*evidence was retained/);
  assert.deepEqual(calls, []);
});

test("failure before attempting a launch can remove its temporary profile", async () => {
  const calls = [];
  await cleanupHarness({
    app: undefined, electronProcess: undefined, stopped: false, launchAttempted: false,
    root: "/temporary-test-root", remove: async (path) => { calls.push(path); },
  });
  assert.deepEqual(calls, ["/temporary-test-root"]);
});

test("retained evidence fails the run without masking the error already propagating", async () => {
  const { spawnSync } = await import("node:child_process");
  const { fileURLToPath } = await import("node:url");
  const module = fileURLToPath(new URL("../e2e/harness-cleanup.mjs", import.meta.url));
  const run = (body) => spawnSync(process.execPath, ["--input-type=module", "-e",
    `import { retainEvidence } from ${JSON.stringify(module)}; try { ${body} } finally { retainEvidence("evidence retained at /x"); }`,
  ], { encoding: "utf8", timeout: 5000 });
  const failed = run('throw new Error("the original failure");');
  assert.notEqual(failed.status, 0);
  assert.match(failed.stderr, /the original failure/);
  assert.match(failed.stderr, /evidence retained at \/x/);
  const passed = run("");
  assert.equal(passed.status, 1);
  assert.match(passed.stderr, /evidence retained at \/x/);
});
