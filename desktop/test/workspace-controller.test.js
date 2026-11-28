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

test("a shared workspace address requires a matching separate private sign-in", async () => {
  const f = fixture();
  const shared = `https://heinzel.team/open/#server=${encodeURIComponent("https://studio.example")}`;
  const pending = await f.controller.connect({ kind: "link", link: shared });
  assert.equal(pending.busy, false);
  assert.equal(pending.needsSignIn, true);
  assert.match(pending.message, /studio.example/);
  assert.equal(f.events.some((event) => event[0] === "verify" || event[0] === "replace"), false);
  const wrong = await f.controller.connect({ kind: "link", link: `https://other.example/#token=${token}` });
  assert.equal(wrong.needsSignIn, true);
  assert.equal(f.events.some((event) => event[0] === "verify" || event[0] === "replace"), false);
  assert.equal((await f.controller.connect(request)).current, "https://studio.example");
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

test("a newer selection supersedes one still verifying", async () => {
  let finish;
  const f = fixture({ verify: () => new Promise((resolve) => { finish = resolve; }) });
  const pending = f.controller.connect(request);
  assert.equal((await f.controller.connect({ kind: "local" })).busy, false);
  finish(); await pending;
  assert.equal(f.controller.state().current, local.origin);
  assert.equal(f.events.some((event) => event[0] === "replace"), false);
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

test("remote preload exposes only appearance and fixed workspace menu actions", () => {
  let exposed;
  runInNewContext(readFileSync(new URL("../src/workspace-remote-preload.cjs", import.meta.url), "utf8"), {
    navigator: { platform: "MacIntel" },
    process: { platform: "darwin" },
    require(name) { assert.equal(name, "electron"); return { contextBridge: { exposeInMainWorld: (_key, value) => { exposed = value; } } }; },
  });
  assert.deepEqual(Object.keys(exposed).sort(), ["clientFlavor", "clientOS", "remoteWorkspace", "titlebar", "workspaces"]);
  assert.deepEqual(Object.keys(exposed.workspaces).sort(), ["currentName", "open"]);
  assert.equal(exposed.clientFlavor, "electron");
  assert.equal(exposed.clientOS, "macOS");
  assert.equal(exposed.remoteWorkspace, true);
  assert.equal(exposed.titlebar.left, 80);
});

test("a rejected saved credential requests sign-in for that candidate and retains the current workspace", async () => {
  const origin = "https://studio.example";
  const f = fixture({ directory: { list: () => [], connection: () => ({ origin, token, remote: true }) },
    verify: async () => { throw { status: 401 }; } });
  const result = await f.controller.connect({ kind: "recent", origin });
  assert.equal(result.signInOrigin, origin);
  assert.equal(result.needsSignIn, true);
  assert.equal(result.current, local.origin);
  assert.equal(result.canRetry, false);
  f.controller.cancel();
  assert.equal(f.controller.state().signInOrigin, null);
});

test("selecting an unsigned saved workspace focuses its empty sign-in sheet and cancel restores the row", async () => {
  const origin = "https://studio.example";
  const { controller } = fixture({ directory: {
    list: () => [{ origin, name: "Studio", signedIn: false }], connection: () => null,
  } });
  const elements = new Map();
  const document = {
    activeElement: null,
    createElement() {
      return {
        children: [], dataset: {}, classList: { toggle() {} }, setAttribute() {},
        addEventListener(name, handler) { this[name] = handler; },
        append(...children) { this.children.push(...children); },
        replaceChildren(...children) { this.children = children; },
        showModal() { this.open = true; }, close() { this.open = false; },
        focus() { document.activeElement = this; },
      };
    },
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, this.createElement());
      return elements.get(id);
    },
    querySelectorAll() { return this.getElementById("rows").children.flatMap((row) => row.children); },
  };
  let opened = 0, cancelled = 0;
  runInNewContext(readFileSync(new URL("../src/workspace-picker.js", import.meta.url), "utf8"), {
    document, navigator: {}, window: { addEventListener() {}, workspacePicker: {
      onState() {}, async state() { return controller.state(); },
      async edit() { opened++; }, async cancel() { cancelled++; controller.cancel(); },
      recent() { assert.fail("an unsigned workspace must wait for a sign-in URL"); },
    } },
  });
  await new Promise(setImmediate);
  const get = (id) => document.getElementById(id);
  const choose = document.querySelectorAll().find((node) => node.dataset.key === `choose-${origin}`);
  assert.ok(choose);
  choose.click();
  assert.equal(get("sheet").open, true);
  assert.equal(get("sheet-title").textContent, "Sign in");
  assert.equal(document.activeElement, get("link"));
  assert.equal(get("link").value, "");
  assert.equal(get("submit").disabled, true);
  assert.equal(get("paste").hidden, false);
  assert.equal(get("retry").hidden, true);
  assert.equal(opened, 1);
  get("cancel").onclick();
  assert.equal(get("sheet").open, false);
  assert.equal(document.activeElement, choose);
  assert.equal(cancelled, 1);
  assert.equal(controller.state().current, local.origin);
});
