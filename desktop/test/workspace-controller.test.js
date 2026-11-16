import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { createWorkspaceController, isPickerFrame, workspaceProblem } from "../src/workspace-controller.js";

const token = "r".repeat(32);
const local = { origin: "http://127.0.0.1:1234", token: "l".repeat(32) };
function fixture(overrides = {}) {
  const events = [];
  let current = local.origin;
  const controller = createWorkspaceController({
    directory: { list: () => [], connection: () => null, remember: () => true, forget: () => true },
    local: async () => { events.push("local"); return local; },
    verify: async (target) => { events.push(["verify", target.origin]); },
    replace: async (target) => { events.push(["replace", target.origin]); current = target.origin; },
    changed: (value) => { events.push(["state", value.busy]); assert.equal(JSON.stringify(value).includes(token), false); },
    current: () => current,
    ...overrides,
  });
  return { controller, events };
}
const request = { kind: "link", link: `https://studio.example/#token=${token}` };

test("recovery distinguishes TLS, offline, timeout, denied and missing hosts", () => {
  assert.match(workspaceProblem({ cause: { code: "CERT_HAS_EXPIRED" } }), /Secure connection unavailable/);
  assert.match(workspaceProblem({ code: "ENETUNREACH" }), /offline/);
  assert.match(workspaceProblem({ name: "TimeoutError" }), /Still trying/);
  assert.match(workspaceProblem({ status: 403 }), /denied access/);
  assert.match(workspaceProblem({ status: 404 }), /No Heinzel workspace/);
});

test("switch verifies the explicit remote before replacing and never starts local", async () => {
  const f = fixture();
  const state = await f.controller.connect(request);
  assert.equal(state.current, "https://studio.example");
  assert.deepEqual(f.events, [["state", true], ["verify", "https://studio.example"], ["replace", "https://studio.example"], ["state", false]]);
  assert.equal(state.message, "Connected");
});

test("authentication failure retains the existing workspace and requires a fresh sign-in", async () => {
  let denied = true;
  const f = fixture({ verify: async () => { if (denied) throw { status: 401 }; } });
  const failed = await f.controller.connect(request);
  assert.equal(failed.current, local.origin);
  assert.match(failed.message, /Sign-in required/);
  assert.equal(failed.canRetry, false);
  assert.equal(failed.needsSignIn, true);
  denied = false;
  assert.equal((await f.controller.connect(request)).current, "https://studio.example");
});

test("selecting the current local or recent workspace keeps its renderer", async () => {
  const f = fixture({ directory: { list: () => [], connection: () => local, remember: () => true, forget: () => true } });
  assert.equal((await f.controller.connect({ kind: "local" })).busy, false);
  assert.equal((await f.controller.connect({ kind: "recent", origin: local.origin })).current, local.origin);
  assert.equal(f.events.some((event) => event[0] === "replace"), false);
});

test("concurrent transitions are rejected while one verifies", async () => {
  let finish;
  const f = fixture({ verify: () => new Promise((resolve) => { finish = resolve; }) });
  const pending = f.controller.connect(request);
  assert.equal((await f.controller.connect({ kind: "local" })).busy, true);
  assert.equal(f.events.includes("local"), false);
  finish(); await pending;
});

test("invalid links and unavailable recent credentials cannot switch", async () => {
  const f = fixture();
  assert.match((await f.controller.connect({ kind: "link", link: "http://127.0.0.1" })).message, /HTTPS/);
  const unsigned = await f.controller.connect({ kind: "recent", origin: "https://studio.example" });
  assert.match(unsigned.message, /Sign-in required/);
  assert.equal(unsigned.needsSignIn, true);
  assert.equal(unsigned.canRetry, false);
  assert.equal((await f.controller.connect({ kind: "link", link: "invalid" })).busy, false);
  assert.equal(f.controller.state().current, local.origin);
  assert.equal(f.events.some((event) => event[0] === "replace"), false);
});

test("a crashed renderer remains retryable when its host is reachable", async () => {
  const f = fixture();
  f.controller.failed(local, new Error("renderer crashed"));
  const state = await f.controller.inspect(local);
  assert.equal(state.canRetry, true);
  assert.match(state.message, /Connection restored/);
  assert.equal((await f.controller.retry()).canRetry, false);
  assert.deepEqual(f.events.filter((event) => event[0] === "replace"), [["replace", local.origin]]);
});

test("explicitly selecting a crashed local workspace also replaces its renderer", async () => {
  const f = fixture();
  f.controller.failed(local, new Error("renderer crashed"));
  await f.controller.connect({ kind: "local" });
  assert.deepEqual(f.events.filter((event) => event[0] === "replace"), [["replace", local.origin]]);
});

test("forgetting a failed workspace also discards its pending retry credential", async () => {
  const f = fixture({ verify: async () => { throw new Error("offline"); } });
  await f.controller.connect(request);
  assert.equal(f.controller.state().canRetry, true);
  assert.equal(f.controller.forget("https://studio.example").canRetry, false);
  const events = f.events.length;
  await f.controller.retry();
  assert.equal(f.events.length, events);
  assert.equal(f.controller.state().current, local.origin);
});

test("picker IPC accepts only its exact living main frame", () => {
  const url = "file:///fixture/workspace-picker.html";
  const frame = { url, isDestroyed: () => false, detached: false };
  const contents = { isDestroyed: () => false, mainFrame: frame };
  const picker = { isDestroyed: () => false, webContents: contents };
  const event = { sender: contents, senderFrame: frame };
  assert.equal(isPickerFrame(event, picker, url), true);
  assert.equal(isPickerFrame({ ...event, senderFrame: { ...frame } }, picker, url), false);
  assert.equal(isPickerFrame({ ...event, sender: { ...contents } }, picker, url), false);
  frame.url = "https://studio.example";
  assert.equal(isPickerFrame(event, picker, url), false);
  frame.url = url; frame.detached = true;
  assert.equal(isPickerFrame(event, picker, url), false);
});

test("remote preload exposes appearance only and cannot invoke IPC", () => {
  let exposed;
  runInNewContext(readFileSync(new URL("../src/workspace-remote-preload.cjs", import.meta.url), "utf8"), {
    navigator: { platform: "MacIntel" },
    require(name) { assert.equal(name, "electron"); return { contextBridge: { exposeInMainWorld: (_key, value) => { exposed = value; } } }; },
  });
  assert.deepEqual(Object.keys(exposed).sort(), ["remoteWorkspace", "titlebar"]);
  assert.equal(exposed.remoteWorkspace, true);
  assert.equal(exposed.titlebar.left, 80);
});

test("picker directs an unsigned recent workspace to the fresh link field", async () => {
  const elements = new Map();
  const element = () => ({ classList: { toggle() {} }, dataset: {}, addEventListener() {},
    append() {}, replaceChildren() {}, setAttribute() {}, focus() { this.focused = true; },
    scrollIntoView() { this.scrolled = true; } });
  const byId = (id) => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
  let publish;
  const state = await fixture().controller.connect({ kind: "recent", origin: "https://studio.example" });
  runInNewContext(readFileSync(new URL("../src/workspace-picker.js", import.meta.url), "utf8"), {
    window: { workspacePicker: { onState: (handler) => { publish = handler; }, state: async () => state } },
    document: { getElementById: byId, createElement: element, querySelectorAll: () => [] },
  });
  await new Promise(setImmediate);
  assert.equal(byId("retry").hidden, true);
  assert.equal(byId("local").disabled, true);
  assert.equal(byId("link").focused, true);
  assert.equal(byId("link").scrolled, true);
  assert.equal(typeof publish, "function");
});
