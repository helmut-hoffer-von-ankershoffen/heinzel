import assert from "node:assert/strict";
import test from "node:test";
import { createNativePermissions } from "../src/native-permissions.js";

function fixture() {
  const connection = { origin: "http://127.0.0.1:30123", token: "synthetic_native_ui_test_token_1234567890" };
  const frame = { isDestroyed: () => false, detached: false, processId: 1, frameToken: "main", url: `${connection.origin}/` };
  const contents = { isDestroyed: () => false, mainFrame: frame };
  const window = { isDestroyed: () => false };
  const windows = new WeakSet([window]);
  const details = { isMainFrame: true, requestingUrl: frame.url };
  let resolvePrompt, prompts = [];
  const adapter = createNativePermissions({ windows, fromWebContents: () => window, connection: () => connection,
    prompt: (owner, options) => { prompts.push({ owner, options }); return new Promise((resolve) => { resolvePrompt = resolve; }); } });
  const invoke = (permission = "notifications", requestDetails = details, source = contents) =>
    new Promise((resolve) => adapter.request(source, permission, resolve, requestDetails));
  return { connection, frame, contents, window, details, adapter, invoke, prompts, answer: (response) => resolvePrompt({ response }) };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));

test("a native fixed prompt grants only the matching current document", async () => {
  const f = fixture();
  assert.equal(f.adapter.check(), false);
  const decision = f.invoke();
  await tick();
  assert.equal(f.prompts.length, 1);
  assert.equal(f.prompts[0].owner, f.window);
  assert.match(f.prompts[0].options.title, /notifications/i);
  f.answer(1);
  assert.equal(await decision, true);
  assert.equal(f.adapter.check(f.contents, "notifications", f.connection.origin, f.details), true);
  assert.equal(f.adapter.check(f.contents, "notifications", "http://evil.example", f.details), false);
  assert.equal(f.adapter.check(f.contents, "notifications", f.connection.origin, { ...f.details, isMainFrame: false }), false);
  assert.equal(f.adapter.check(f.contents, "media", f.connection.origin, { ...f.details, mediaType: "audio" }), false);
  f.adapter.revoke();
  assert.equal(f.adapter.check(f.contents, "notifications", f.connection.origin, f.details), false);
  await tick();
  const denied = f.invoke();
  await tick(); f.answer(0);
  assert.equal(await denied, false);
});

test("deny unknown permissions, mixed media, subframes, foreign origins and missing provenance", async () => {
  const f = fixture();
  for (const [permission, details] of [
    ["display-capture", f.details], ["media", { ...f.details, mediaTypes: ["audio", "video"] }],
    ["media", { ...f.details, mediaTypes: ["video"] }], ["media", f.details],
    ["notifications", { ...f.details, isMainFrame: false }], ["notifications", { ...f.details, requestingUrl: "http://evil.example/" }],
    ["notifications", {}],
  ]) assert.equal(await f.invoke(permission, details), false);
  assert.equal(f.prompts.length, 0);
  const audio = f.invoke("media", { ...f.details, mediaTypes: ["audio"] });
  await tick(); assert.match(f.prompts[0].options.title, /microphone/i); f.answer(1);
  assert.equal(await audio, true);
});

test("close, navigation, frame replacement, revocation and competing requests cannot inherit a decision", async () => {
  for (const change of [
    (f) => { f.window.isDestroyed = () => true; },
    (f) => { f.frame.url += "other"; },
    (f) => { f.contents.mainFrame = { ...f.frame, frameToken: "new" }; },
    (f) => { f.adapter.revoke(); },
  ]) {
    const f = fixture();
    const first = f.invoke(); await tick();
    assert.equal(await f.invoke(), false);
    change(f); f.answer(1);
    assert.equal(await first, false);
  }
});

test("prompt failures deny and do not strand a pending request", async () => {
  const f = fixture();
  const adapter = createNativePermissions({ windows: new WeakSet([f.window]), fromWebContents: () => f.window,
    connection: () => f.connection, prompt: () => { throw new Error("dialog failed"); } });
  for (let i = 0; i < 2; i++) {
    assert.equal(await new Promise((resolve) => adapter.request(f.contents, "notifications", resolve, f.details)), false);
  }
});

test("revocation before the native prompt prevents even opening it", async () => {
  const f = fixture();
  const request = f.invoke();
  f.adapter.revoke();
  assert.equal(await request, false);
  await tick();
  assert.equal(f.prompts.length, 0);
});

test("a conflicting media security origin is rejected", async () => {
  const f = fixture();
  assert.equal(await f.invoke("media", { ...f.details, mediaTypes: ["audio"], securityOrigin: "https://elsewhere.invalid" }), false);
  assert.equal(f.prompts.length, 0);
});


test("revocation does not permit another prompt while the old native dialog remains open", async () => {
  const f = fixture();
  const first = f.invoke(); await tick();
  f.adapter.revoke();
  assert.equal(await first, false);
  assert.equal(await f.invoke(), false);
  assert.equal(f.prompts.length, 1);
  f.answer(1); await tick();
  const later = f.invoke(); await tick();
  assert.equal(f.prompts.length, 2);
  f.answer(0); assert.equal(await later, false);
});

test("Electron media security origin permits its canonical trailing slash only", async () => {
  const f = fixture();
  for (const securityOrigin of [`${f.connection.origin}/elsewhere`, `${f.connection.origin}/?other`]) {
    assert.equal(await f.invoke("media", { ...f.details, mediaTypes: ["audio"], securityOrigin }), false);
  }
  const decision = f.invoke("media", { ...f.details, mediaTypes: ["audio"], securityOrigin: `${f.connection.origin}/` });
  await tick();
  f.answer(0);
  assert.equal(await decision, false);
  assert.equal(f.prompts.length, 1);
});

test("microphone status is limited to audio and the current document and connection", async () => {
  const f = fixture();
  const details = { ...f.details, mediaTypes: ["audio"], securityOrigin: `${f.connection.origin}/` };
  const decision = f.invoke("media", details); await tick(); f.answer(1); assert.equal(await decision, true);
  const status = { ...f.details, mediaType: "audio", securityOrigin: `${f.connection.origin}/` };
  assert.equal(f.adapter.check(f.contents, "media", f.connection.origin, status), true);
  assert.equal(f.adapter.check(f.contents, "media", f.connection.origin, { ...status, mediaType: "video" }), false);
  assert.equal(f.adapter.check(f.contents, "media", f.connection.origin, { ...status, requestingUrl: undefined }), false);
  f.connection.token = "different";
  assert.equal(f.adapter.check(f.contents, "media", f.connection.origin, status), false);
});
