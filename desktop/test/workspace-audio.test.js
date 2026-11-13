import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { createWorkspaceAudio, systemAudioAvailable } from "../src/workspace-audio.js";

function fixture({ system = false } = {}) {
  const origin = "http://127.0.0.1:8737";
  const connection = { origin, token: "a".repeat(32) };
  const frame = { url: `${origin}/`, processId: 7, frameToken: "owned-main", detached: false, isDestroyed: () => false };
  const contents = { mainFrame: frame, isDestroyed: () => false };
  const window = { webContents: contents, isDestroyed: () => false, isFocused: () => true, isVisible: () => true };
  const windows = new WeakSet([window]);
  const prompts = [];
  let resolve;
  const events = [];
  let clock = 1000;
  const controller = createWorkspaceAudio({ windows, system, now: () => clock,
    log: (event, fields) => events.push({ event, ...fields }),
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
    events, tick: (ms) => { clock += ms; },
    sender: { sender: contents, senderFrame: frame },
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

test("system loopback is offered on Windows and macOS 14.2+ only", () => {
  assert.equal(systemAudioAvailable("win32", "10.0.22631"), true);
  for (const release of ["23.2.0", "23.6.0", "24.0.0", "25.1.0"]) assert.equal(systemAudioAvailable("darwin", release), true);
  for (const release of ["23.1.0", "22.6.0", "21.0.0", "", "x"]) assert.equal(systemAudioAvailable("darwin", release), false);
  assert.equal(systemAudioAvailable("linux", "6.8.0"), false);
});

test("a chosen system source loops back system audio with the own frame as video, no picker", async () => {
  const f = fixture({ system: true });
  assert.equal(f.controller.available(), true);
  assert.equal(f.controller.choose(f.sender, "system"), true);
  f.start(); await settled();
  assert.equal(f.prompts[0].options.title, "Share system audio?");
  assert.match(f.prompts[0].options.detail, /workspace is briefly shared as video, then video is stopped/);
  assert.equal(f.prompts[0].options.defaultId, 0);
  f.reply(1); await settled();
  assert.deepEqual(f.responses, [{ video: f.frame, audio: "loopback" }]);
  assert.equal(f.controller.choose(f.sender, "workspace"), true);
  f.start(); await settled();
  assert.equal(f.prompts[1].options.title, "Share workspace audio?", "a choice arms exactly one capture");
  f.reply(1); await settled();
  assert.deepEqual(f.responses[1], { video: f.frame, audio: f.frame, enableLocalEcho: true });
});

test("system audio is never granted where unavailable, unowned, expired or revoked", async () => {
  const off = fixture();
  assert.equal(off.controller.available(), false);
  assert.equal(off.controller.choose(off.sender, "system"), false);
  off.start(); await settled();
  assert.deepEqual(off.responses, [{}], "a refused system choice never becomes a workspace grant");
  assert.equal(off.prompts.length, 0);
  off.start(); await settled(); off.reply(1); await settled();
  assert.equal(off.prompts[0].options.title, "Share workspace audio?", "the spent choice denies exactly one request");
  assert.deepEqual(off.responses[1], { video: off.frame, audio: off.frame, enableLocalEcho: true });
  const cases = {
    "unknown source": f => f.controller.choose(f.sender, "screen"),
    "foreign sender": f => f.controller.choose({ sender: { ...f.contents }, senderFrame: f.frame }, "system"),
    "subframe sender": f => f.controller.choose({ sender: f.contents, senderFrame: { ...f.frame, frameToken: "iframe" } }, "system"),
    "background window": f => { f.window.isFocused = () => false; const armed = f.controller.choose(f.sender, "system"); f.window.isFocused = () => true; return armed; },
  };
  for (const [name, arm] of Object.entries(cases)) {
    const f = fixture({ system: true });
    assert.equal(arm(f), false, name);
    f.start(); await settled();
    assert.deepEqual(f.responses, [{}], name);
    assert.equal(f.prompts.length, 0, name);
  }
  for (const cancel of [f => f.tick(10_001), f => f.controller.revoke(), f => f.controller.navigation({ isMainFrame: true })]) {
    const f = fixture({ system: true });
    assert.equal(f.controller.choose(f.sender, "system"), true);
    cancel(f);
    f.start(); await settled();
    if (f.prompts.length) { f.reply(1); await settled(); }
    assert.deepEqual(f.responses[0], {});
    assert.equal(f.prompts.length, 0);
  }
});

test("workspace audio stays the default for an unarmed request, also from a web build without audioSource", async () => {
  // Version skew: an older web build never calls `choose`; a new system-capable desktop keeps its workspace path.
  const f = fixture({ system: true });
  f.start(); await settled();
  assert.equal(f.prompts[0].options.title, "Share workspace audio?");
  f.reply(1); await settled();
  assert.deepEqual(f.responses, [{ video: f.frame, audio: f.frame, enableLocalEcho: true }]);
  assert.deepEqual(f.events.map((e) => e.source), ["workspace", "workspace"]);
  // An expired workspace choice is still the default workspace path, never system.
  assert.equal(f.controller.choose(f.sender, "workspace"), true);
  f.tick(10_001); f.start(); await settled();
  assert.equal(f.prompts[1].options.title, "Share workspace audio?");
  f.reply(1); await settled();
  assert.deepEqual(f.responses[1], { video: f.frame, audio: f.frame, enableLocalEcho: true });
});

test("a spent system choice is denied once with a logged reason; a fresh choice replaces it", async () => {
  for (const spend of [f => f.tick(10_001), f => f.controller.revoke(), f => f.controller.navigation({ isMainFrame: true })]) {
    const f = fixture({ system: true });
    f.controller.choose(f.sender, "system"); spend(f);
    f.start(); await settled();
    assert.deepEqual(f.responses, [{}]);
    assert.deepEqual(f.events.at(-1), { event: "desktop.audio.capture", operation: "audio_capture", step: "request",
      phase: "end", source: "system", outcome: "skipped", reason: "expired" });
    f.controller.choose(f.sender, "system"); f.controller.revoke();
    assert.equal(f.controller.choose(f.sender, "workspace"), true, "a fresh workspace choice replaces a spent system one");
    f.start(); await settled();
    assert.equal(f.prompts[0].options.title, "Share workspace audio?");
    f.reply(1); await settled();
    assert.deepEqual(f.responses[1], { video: f.frame, audio: f.frame, enableLocalEcho: true });
  }
});

test("revocation denies a pending system consent even if the dialog later approves", async () => {
  const f = fixture({ system: true });
  assert.equal(f.controller.choose(f.sender, "system"), true);
  f.start(); await settled();
  f.controller.revoke();
  assert.deepEqual(f.responses, [{}]);
  f.reply(1); await settled();
  assert.deepEqual(f.responses, [{}], "late approval cannot grant or reply a second time");
});

test("a refused source choice logs one skipped outcome with its reason", () => {
  const off = fixture();
  off.controller.choose(off.sender, "system");
  const cases = {
    unknown_source: f => f.controller.choose(f.sender, "screen"),
    subframe: f => f.controller.choose({ sender: f.contents, senderFrame: { ...f.frame, frameToken: "iframe" } }, "system"),
    unowned: f => f.controller.choose({ sender: { ...f.contents }, senderFrame: f.frame }, "system"),
  };
  const reasons = { unavailable: off.events };
  for (const [reason, arm] of Object.entries(cases)) {
    const f = fixture({ system: true });
    assert.equal(arm(f), false, reason);
    reasons[reason] = f.events;
  }
  for (const [reason, events] of Object.entries(reasons))
    assert.deepEqual(events, [{ event: "desktop.audio.source", operation: "audio_capture", step: "source",
      outcome: "skipped", reason }], reason);
});

test("each capture logs one start and one outcome with ADR 0138 fields and no secrets", async () => {
  const f = fixture({ system: true });
  f.controller.choose(f.sender, "system");
  f.start(); await settled(); f.tick(25); f.reply(1); await settled();
  assert.deepEqual(f.events, [
    { event: "desktop.audio.source", operation: "audio_capture", step: "source", source: "system", outcome: "ok" },
    { event: "desktop.audio.capture", operation: "audio_capture", step: "consent", phase: "start", source: "system" },
    { event: "desktop.audio.capture", operation: "audio_capture", step: "consent", phase: "end", source: "system", outcome: "ok", duration_ms: 25 },
  ]);
  const g = fixture({ system: true }); g.controller.choose(g.sender, "system");
  g.start(); await settled(); g.tick(7); g.reply(0); await settled();
  assert.deepEqual(g.events.at(-1), { event: "desktop.audio.capture", operation: "audio_capture", step: "consent", phase: "end",
    source: "system", outcome: "skipped", reason: "declined", duration_ms: 7 });
  assert.deepEqual(g.responses, [{}]);
  const h = fixture(); h.request.userGesture = false; h.start(); await settled();
  assert.deepEqual(h.events, [{ event: "desktop.audio.capture", operation: "audio_capture", step: "request", phase: "end",
    source: "workspace", outcome: "skipped", reason: "unowned" }]);
  for (const e of [...f.events, ...g.events, ...h.events]) assert.doesNotMatch(JSON.stringify(e), /aaaa|127\.0\.0\.1|token/);
});

const mainSource = await readFile(new URL("../src/main.js", import.meta.url), "utf8");
const preloadSource = await readFile(new URL("../src/preload.cjs", import.meta.url), "utf8");

test("main wires loopback capability from the OS release and routes both audio channels to the controller", () => {
  assert.match(mainSource, /system: systemAudioAvailable\(process\.platform, release\(\)\)/);
  assert.match(mainSource, /ipcMain\.handle\("hz:system-audio", \(\) => workspaceAudio\?\.available\(\) === true\)/);
  assert.match(mainSource, /ipcMain\.handle\("hz:audio-source", \(event, source\) => workspaceAudio\?\.choose\(event, source\) === true\)/);
  assert.match(mainSource, /setDisplayMediaRequestHandler\(audio\.display\)/);
  assert.match(mainSource, /window\.on\("blur", audio\.revoke\)/);
});

/** The real preload bridge, its IPC routed to fake `ipcMain` handlers shaped like main.js, and the real controller. */
function bridged({ system }) {
  const f = fixture({ system });
  const handlers = {
    "hz:system-audio": () => f.controller.available() === true,
    "hz:audio-source": (event, source) => f.controller.choose(event, source) === true,
  };
  const ipcRenderer = {
    invoke: async (channel, ...args) => handlers[channel]?.(f.sender, ...args) ?? null,
    on: () => {}, removeListener: () => {},
  };
  const exposed = {};
  runInNewContext(preloadSource, {
    require: () => ({ contextBridge: { exposeInMainWorld: (name, value) => { exposed[name] = value; } }, ipcRenderer }),
    globalThis: { navigator: { platform: "MacIntel" } },
    document: { addEventListener: () => {} },
  });
  return { ...f, bridge: exposed.heinzelDesktop };
}

test("bridge to grant: the opted-in system choice yields one consented loopback, then workspace again", async () => {
  const f = bridged({ system: true });
  assert.equal(await f.bridge.systemAudio(), true);
  assert.equal(await f.bridge.audioSource("system"), true);
  f.start(); await settled();
  assert.equal(f.prompts[0].options.title, "Share system audio?");
  f.reply(1); await settled();
  assert.deepEqual(f.responses, [{ video: f.frame, audio: "loopback" }]);
  // The choice was single-use: the next capture, armed or not, is workspace audio with its own consent.
  f.start(); await settled();
  assert.equal(f.prompts[1].options.title, "Share workspace audio?");
  f.reply(1); await settled();
  assert.deepEqual(f.responses[1], { video: f.frame, audio: f.frame, enableLocalEcho: true });
});

test("bridge to grant: a desktop without loopback refuses system and still shares workspace audio", async () => {
  const f = bridged({ system: false });
  assert.equal(await f.bridge.systemAudio(), false);
  assert.equal(await f.bridge.audioSource("system"), false);
  assert.equal(await f.bridge.audioSource("workspace"), true);
  f.start(); await settled();
  assert.equal(f.prompts[0].options.title, "Share workspace audio?");
  f.reply(1); await settled();
  assert.deepEqual(f.responses, [{ video: f.frame, audio: f.frame, enableLocalEcho: true }]);
});

test("bridge to grant: a coerced unknown source arms workspace, never loopback", async () => {
  const f = bridged({ system: true });
  assert.equal(await f.bridge.audioSource("screen"), true);
  f.start(); await settled();
  assert.equal(f.prompts[0].options.title, "Share workspace audio?");
  f.reply(1); await settled();
  assert.notEqual(f.responses[0].audio, "loopback");
});
