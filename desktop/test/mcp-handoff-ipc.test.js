import assert from "node:assert/strict";
import test from "node:test";
import { createMcpHandoffHandler } from "../src/mcp-handoff-ipc.js";

const id = "a".repeat(32);
const handoff = `1794220000.${"b".repeat(64)}`;
const plan = { id, action: "check", session: "terminal", registration: "fixture", handoff };

function fixture({ body = plan, confirm = true } = {}) {
  const origin = "http://127.0.0.1:30123";
  const connection = { origin, token: "synthetic_native_ui_test_token_1234567890" };
  const frame = { isDestroyed: () => false, processId: 1, frameToken: "main", detached: false, url: origin };
  const sender = { mainFrame: frame, isDestroyed: () => false };
  const window = { isDestroyed: () => false };
  const received = { requests: [], prompts: [], copies: [] };
  const handle = createMcpHandoffHandler({
    windows: new WeakSet([window]), fromWebContents: () => window, connection: () => connection,
    fetchPlan: async (url, options) => {
      received.requests.push(url);
      assert.equal(options.headers.Authorization, `Bearer ${connection.token}`);
      assert.equal(options.redirect, "error");
      return { ok: true, json: async () => body };
    },
    prompt: async (owner, options) => {
      assert.equal(owner, window);
      received.prompts.push(options);
      return { response: typeof confirm === "function" ? confirm(frame) : Number(confirm) };
    },
    copy: (value) => received.copies.push(value),
  });
  return { handle, event: { sender, senderFrame: frame }, received, origin };
}

test("uses the shell's connection to resolve a locator and copies only after a native choice", async () => {
  const { handle, event, received, origin } = fixture();
  assert.equal(await handle(event, id), true);
  assert.deepEqual(received.requests, [`${origin}/api/mcp/handoffs/${id}`]);
  assert.deepEqual(received.prompts[0].buttons, ["Cancel", "Copy command"]);
  assert.deepEqual(received.copies, [`hz system mcp check fixture --handoff ${handoff}`]);
  const owner = fixture({ body: { ...plan, action: "stop", session: "owning-session" } });
  assert.equal(await owner.handle(owner.event, id), true);
  assert.deepEqual(owner.received.copies, [`stop --handoff ${handoff}`]);
  const install = fixture({ body: { ...plan, action: "install" } });
  assert.equal(await install.handle(install.event, id), true);
  assert.deepEqual(install.received.copies, [`hz system mcp review-install fixture --handoff ${handoff}`]);
  const declined = fixture({ confirm: false });
  assert.equal(await declined.handle(declined.event, id), false);
  assert.deepEqual(declined.received.copies, []);
});

test("refuses wrong frames, forged plan data, and loss of ownership during handoff", async () => {
  const { handle, event, received } = fixture();
  await assert.rejects(handle({ ...event, senderFrame: { ...event.senderFrame, frameToken: "child" } }, id));
  await assert.rejects(handle(event, id + "\n"));
  await assert.rejects(handle(event, { id, action: "stop", approved: true }));
  assert.deepEqual(received.requests, []);
  for (const body of [{ ...plan, id: "c".repeat(32) }, { ...plan, registration: "fixture\n" },
    { ...plan, action: "stop" }, { ...plan, handoff: `${handoff}; echo substituted` }]) {
    const invalid = fixture({ body });
    await assert.rejects(invalid.handle(invalid.event, id));
    assert.deepEqual(invalid.received.prompts, []);
  }
  const moved = fixture({ confirm: (frame) => { frame.detached = true; return 1; } });
  await assert.rejects(moved.handle(moved.event, id));
  assert.deepEqual(moved.received.copies, []);
});
