import assert from "node:assert/strict";
import test from "node:test";
import { createWorkspaceAudio } from "../src/workspace-audio.js";

function fixture() {
  const origin = "http://127.0.0.1:8737";
  const connection = { origin, token: "a".repeat(32) };
  const frame = { url: `${origin}/`, processId: 7, frameToken: "owned-main", detached: false, isDestroyed: () => false };
  const contents = { mainFrame: frame, isDestroyed: () => false };
  const window = { webContents: contents, isDestroyed: () => false, isFocused: () => true, isVisible: () => true };
  const windows = new WeakSet([window]);
  const prompts = [];
  let resolve;
  const controller = createWorkspaceAudio({ windows,
    fromWebContents: (value) => value === contents ? window : null,
    fromFrame: (value) => value === frame ? contents : null,
    connection: () => connection,
    prompt: (owner, options) => { prompts.push({ owner, options }); return new Promise((done) => { resolve = done; }); },
  });
  const request = { frame, securityOrigin: origin, userGesture: true, audioRequested: true, videoRequested: true };
  const responses = [];
  return { controller, connection, contents, frame, window, windows, request, prompts, responses,
    start: () => controller.display(request, value => responses.push(value)),
    reply: (response) => resolve({ response }),
    details: { isMainFrame: true, requestingUrl: frame.url, securityOrigin: origin }, origin };
}
const settled = () => new Promise(setImmediate);

test("fresh native consent defaults to deny and captures only the exact requesting frame", async () => {
  const f = fixture();
  f.start(); await settled();
  assert.equal(f.prompts[0].owner, f.window);
  assert.equal(f.prompts[0].options.defaultId, 0);
  assert.equal(f.prompts[0].options.cancelId, 0);
  assert.deepEqual(f.responses, []);
  f.reply(1); await settled();
  assert.deepEqual(f.responses, [{ video: f.frame, audio: f.frame, enableLocalEcho: true }]);
  f.start(); await settled();
  assert.equal(f.prompts.length, 2, "there is no persistent grant");
  f.reply(0); await settled();
  assert.deepEqual(f.responses[1], {});
});

const changes = {
  "missing frame": f => { f.request.frame = null; },
  "foreign frame": f => { f.request.frame = { ...f.frame, frameToken: "iframe" }; },
  "changed main token": f => { f.contents.mainFrame = { ...f.frame, frameToken: "new" }; },
  "changed main process": f => { f.contents.mainFrame = { ...f.frame, processId: 8 }; },
  "detached frame": f => { f.frame.detached = true; },
  "destroyed frame": f => { f.frame.isDestroyed = () => true; },
  "destroyed contents": f => { f.contents.isDestroyed = () => true; },
  "destroyed window": f => { f.window.isDestroyed = () => true; },
  "background window": f => { f.window.isFocused = () => false; },
  "hidden window": f => { f.window.isVisible = () => false; },
  "unowned window": f => { f.windows.delete(f.window); },
  "non-owner contents": f => { f.window.webContents = { ...f.contents }; },
  "foreign origin": f => { f.request.securityOrigin = "https://example.com"; },
  "origin path": f => { f.request.securityOrigin = `${f.origin}/foreign`; },
  "foreign document": f => { f.frame.url = "https://example.com/"; },
};
for (const [name, change] of Object.entries(changes)) {
  test(`rejects ${name} before consent`, async () => {
    const f = fixture(); change(f); f.start(); await settled();
    assert.deepEqual(f.responses, [{}]); assert.equal(f.prompts.length, 0);
  });
  test(`rejects ${name} changed during consent`, async () => {
    const f = fixture(); f.start(); await settled(); change(f); f.reply(1); await settled();
    assert.deepEqual(f.responses, [{}]);
  });
}
for (const field of ["audioRequested", "videoRequested", "userGesture"]) {
  test(`requires explicit ${field}`, async () => {
    const f = fixture(); f.request[field] = false; f.start(); await settled();
    assert.deepEqual(f.responses, [{}]); assert.equal(f.prompts.length, 0);
  });
}
test("cancellation and token rotation refuse late consent exactly once", async () => {
  for (const cancel of [f => f.controller.revoke(), f => f.controller.navigation({ isMainFrame: true }),
    f => { f.connection.token = "b".repeat(32); }]) {
    const f = fixture(); f.start(); await settled(); cancel(f); f.reply(1); await settled();
    assert.deepEqual(f.responses, [{}]);
  }
});
test("only one pending request and pre-prompt revalidation", async () => {
  const f = fixture(); f.start(); f.start(); await settled();
  assert.deepEqual(f.responses, [{}]); assert.equal(f.prompts.length, 1);
  f.reply(1); await settled(); assert.equal(f.responses.length, 2);
  const g = fixture(); g.start(); g.connection.token = "b".repeat(32); await settled();
  assert.deepEqual(g.responses, [{}]); assert.equal(g.prompts.length, 0);
});
test("display permission only admits the owned main document to the separate consent handler", () => {
  const f = fixture();
  assert(f.controller.check(f.contents, "display-capture", f.origin, f.details));
  assert(f.controller.check(f.contents, "display-capture", `${f.origin}/`, f.details));
  for (const kind of ["media", "microphone", "notifications", "unknown"])
    assert.equal(f.controller.check(f.contents, kind, f.origin, f.details), false);
  assert.equal(f.controller.check(f.contents, "display-capture", f.origin, { ...f.details, isMainFrame: false }), false);
  assert.equal(f.controller.check(f.contents, "display-capture", f.origin, {}), false);
  f.controller.request(f.contents, "display-capture", allowed => assert.equal(allowed, true), f.details);
  assert.equal(f.prompts.length, 0);
});

test("Electron 45's completed empty-selection denial is handled once without an unhandled rejection", async () => {
  for (const mode of ["early", "consent", "revoke"]) {
    const f = fixture();
    let callbacks = 0;
    if (mode === "early") f.request.userGesture = false;
    f.controller.display(f.request, streams => {
      callbacks++;
      assert.deepEqual(streams, {});
      throw new TypeError("Video was requested, but no video stream was provided");
    });
    await settled();
    if (mode !== "early") {
      if (mode === "revoke") f.controller.revoke();
      f.reply(mode === "revoke" ? 1 : 0);
      await settled();
    }
    assert.equal(callbacks, 1);
    if (mode !== "early") {
      f.start(); await settled();
      assert.equal(f.prompts.length, 2, "completed denial does not strand pending state");
      f.reply(0); await settled();
    }
  }
});

test("other synchronous callback errors are not mistaken for a completed Electron denial", () => {
  for (const error of [new TypeError("different error"), new Error("Video was requested, but no video stream was provided")]) {
    const f = fixture(); f.request.userGesture = false;
    let callbacks = 0;
    assert.throws(() => f.controller.display(f.request, () => { callbacks++; throw error; }), value => value === error);
    assert.equal(callbacks, 1);
  }
});
