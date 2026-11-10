import assert from "node:assert/strict";
import test from "node:test";
import { createMcpProjectHandler, installMcpProject } from "../src/mcp-project-ipc.js";

function fixture({ cancel = false, consent = true, detached = false, wrongReceipt = false } = {}) {
  const connection = { origin: "http://127.0.0.1:30123", token: "synthetic_native_ui_test_token_1234567890" };
  const frame = { isDestroyed: () => false, processId: 1, frameToken: "main", detached: false, url: connection.origin };
  const sender = { mainFrame: frame, isDestroyed: () => false };
  const window = { isDestroyed: () => false };
  const calls = [];
  const handle = createMcpProjectHandler({
    windows: new WeakSet([window]), fromWebContents: () => window, connection: () => connection,
    choose: async () => ({ canceled: cancel, filePaths: ["/fictional/a'b $(not-run)"] }),
    inspect: () => ({ isDirectory: () => true, dev: 7, ino: 42 }),
    prompt: async (_, options) => { calls.push(options); frame.detached = detached; return { response: Number(consent) }; },
    install: async (...args) => {
      calls.push(args);
      return { configuration: "created", path: wrongReceipt ? "/different/.mcp.json" : "/fictional/a'b $(not-run)/.mcp.json", mcp_readiness: "unverified" };
    },
  });
  return { handle, event: { sender, senderFrame: frame }, calls };
}

test("requires a local picker and exact native confirmation before creating config", async () => {
  const { handle, event, calls } = fixture();
  assert.equal(await handle(event, "claude"), true);
  assert.match(calls[0].detail, /\/fictional\/a'b \$\(not-run\)\/\.mcp\.json/);
  assert.deepEqual(calls[0].buttons, ["Cancel", "Create configuration"]);
  assert.equal(calls[0].defaultId, 0);
  assert.deepEqual(calls[1], ["claude", "/fictional/a'b $(not-run)", 7, 42]);
  for (const options of [{ cancel: true }, { consent: false }]) {
    const declined = fixture(options);
    assert.equal(await declined.handle(declined.event, "claude"), false);
    assert.equal(declined.calls.some(Array.isArray), false);
  }
});

test("rejects forged client, foreign frame and navigation after confirmation", async () => {
  const invalid = fixture();
  await assert.rejects(invalid.handle(invalid.event, "claude;echo"));
  await assert.rejects(invalid.handle({ ...invalid.event, senderFrame: { ...invalid.event.senderFrame, frameToken: "child" } }, "claude"));
  assert.deepEqual(invalid.calls, []);
  const moved = fixture({ detached: true });
  await assert.rejects(moved.handle(moved.event, "claude"));
  assert.equal(moved.calls.some(Array.isArray), false);
  const wrong = fixture({ wrongReceipt: true });
  await assert.rejects(wrong.handle(wrong.event, "claude"), /could not be confirmed/);
});

test("uses fixed argv without a shell and binds the reviewed directory identity", async () => {
  const fake = (command, args, options, done) => {
    assert.equal(command, "/fixture/hz");
    assert.deepEqual(args, ["system", "serve", "mcp-install-project", "--client", "claude", "--project",
      "/fictional/a'b $(not-run)", "--expected-device", "7", "--expected-inode", "42"]);
    assert.equal(options.shell, false);
    assert.equal(options.timeout, 15_000);
    done(null, '{"configuration":"created"}');
  };
  assert.deepEqual(await installMcpProject("/fixture/hz", "claude", "/fictional/a'b $(not-run)", 7, 42, fake),
    { configuration: "created" });
});
