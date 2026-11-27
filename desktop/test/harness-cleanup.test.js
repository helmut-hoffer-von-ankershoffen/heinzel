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

test("native smoke accepts a terminated Electron child while its fixture still holds the output pipes", async (t) => {
  const { spawnSync } = await import("node:child_process");
  const { mkdtempSync, readFileSync, rmSync, writeFileSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const { fileURLToPath } = await import("node:url");
  const dir = mkdtempSync(join(tmpdir(), "heinzel-native-process-test-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const executable = join(dir, "electron-fixture");
  const stoppedReceipt = join(dir, "descendant-stopped");
  const fixture = join(dir, "descendant.mjs");

  // The test-owned descendant retains both inherited pipes until the harness sends STOP.
  writeFileSync(fixture, `
    import { createConnection } from "node:net";
    import { writeFileSync } from "node:fs";
    const token = process.env.HZ_TEST_CONTROL_TOKEN;
    const control = createConnection({ host: "127.0.0.1", port: Number(process.env.HZ_TEST_CONTROL_PORT) });
    let input = "";
    control.on("connect", () => control.write(\`HELLO \${token}\\n\`));
    control.on("data", (chunk) => {
      input += chunk;
      if (input.includes(\`READY \${token}\\n\`)) {
        input = "";
        process.send("ready");
        process.disconnect();
      } else if (input.includes(\`STOP \${token}\\n\`)) {
        writeFileSync(${JSON.stringify(stoppedReceipt)}, "stopped");
        control.end();
      }
    });
    setTimeout(() => control.destroy(), 10_000).unref();
  `);
  writeFileSync(executable, `#!${process.execPath}
    const {spawn} = require("node:child_process");
    const {once} = require("node:events");
    (async () => {
      const fixture = spawn(process.execPath, [${JSON.stringify(fixture)}], {
        stdio: ["ignore", "inherit", "inherit", "ipc"],
      });
      const [state] = await once(fixture, "message");
      if (state !== "ready") process.exit(2);
      const address = new URL(process.env.HZ_TEST_URL);
      const token = new URLSearchParams(address.hash.slice(1)).get("token");
      const response = await fetch(new URL("/native-ready", address), {
        headers: { Authorization: "Bearer " + token },
      });
      if (!response.ok) process.exit(3);
    })().catch((error) => { console.error(error); process.exit(4); });
  `, { mode: 0o700 });

  const script = fileURLToPath(new URL("../e2e/native-startup.mjs", import.meta.url));
  const result = spawnSync(process.execPath, [script], {
    env: { ...process.env, HEINZEL_ELECTRON_EXECUTABLE: executable, CI: "true", HZ_DISPOSABLE_NATIVE_CI: "1" },
    encoding: "utf8",
    timeout: 12_000,
  });
  assert.ifError(result.error);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /made its authenticated lifecycle request/);
  assert.equal(readFileSync(stoppedReceipt, "utf8"), "stopped");
});
