import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import { album, albumIntent, verified, remoteURL, filmResponse, createAlbumTransport } from "../src/album-transport.js";

test("catalog pins all 37 identities and the approved extended digest", () => {
  assert.equal(album.tracks.length, 37);
  assert.equal(new Set(album.tracks.map(t => t.id)).size, 37);
  assert.equal(album.tracks.find(t => t.id === "berlin-evening-extended").media.sha256,
    "306c4b7f00c9f89aa80b624f7dcaeace22c52a14ece3b3f7a1e9b107d6c0f126");
});
test("only catalog selection and narrow intents cross the boundary", () => {
  assert.equal(albumIntent("select", { trackID: album.tracks[0].id }), "select");
  for (const [action, options] of [["select", { trackID: "../bad" }], ["play", { url: "file:///etc/passwd" }],
    ["select", { trackID: album.tracks[0].id, url: "https://example.com" }], ["execute", {}], ["play", []]])
    assert.throws(() => albumIntent(action, options));
});
test("cache verification requires both exact bytes and digest", () => {
  const bytes = Buffer.from("verified fixture");
  const media = { bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex") };
  assert.equal(verified(bytes, media), true);
  assert.equal(verified(Buffer.from("modified fixture"), media), false);
  assert.equal(verified(bytes, { ...media, bytes: 1 }), false);
});


test("remote objects must match their catalog digest exactly", () => {
  assert.equal(remoteURL(album.tracks[0].media), "https://latentheinzels.heinzel.team" + album.tracks[0].media.key);
  assert.throws(() => remoteURL({ ...album.tracks[0].media, key: "/arbitrary.mp4" }));
});
test("film protocol rejects arbitrary paths and methods before filesystem access", async () => {
  const response = await filmResponse(new Request("heinzel-album://film/other.mp4"), "/missing");
  assert.equal(response.status, 404);
});
test("startup is idle and queued requests recheck renderer provenance", async () => {
  const transport = createAlbumTransport({
    app: { getPath: () => "/missing" },
    BrowserWindow: class { constructor() { throw new Error("Unexpected audio startup"); } },
    publish: () => {},
  });
  assert.equal(transport.snapshot().playing, false);
  assert.equal(transport.snapshot().positionSeconds, 0);
  await assert.rejects(transport.handle("play", {}, () => false), /document changed/);
  for (const action of ["download", "pauseDownload", "resumeDownload", "cancelDownload"])
    assert.equal(albumIntent(action), action);
});

function transportFixture() {
  const calls = [], changes = [];
  let windows = 0;
  const media = { currentTime: 0, paused: true, ended: false, volume: 1, muted: false,
    pause() { this.paused = true; }, load() { this.currentTime = 0; },
    async play() { this.paused = false; } };
  const context = vm.createContext({ document: { querySelector: () => media }, window: {} });
  const ready = readFile(new URL("../src/album-audio.js", import.meta.url), "utf8")
    .then(source => vm.runInContext(source, context));
  class AudioWindow {
    constructor() { windows++; }
    isDestroyed() { return false; }
    loadFile() { return Promise.resolve(); }
    webContents = {
      session: { protocol: { handle() {} }, webRequest: { onBeforeRequest() {} } },
      setWindowOpenHandler() {}, on() {},
      async executeJavaScript(script) {
        const [action] = JSON.parse("[" + script.slice(script.indexOf("(") + 1, -1) + "]");
        calls.push(action);
        await ready;
        return vm.runInContext(script, context);
      },
    };
  }
  const transport = createAlbumTransport({ BrowserWindow: AudioWindow,
    app: { getPath: () => "/missing-album-fixture" }, publish: state => changes.push(state),
    fetchObject: async () => { throw new Error("offline fixture"); },
  });
  return { transport, calls, changes, media, windows: () => windows,
    advance: value => { media.currentTime = value; } };
}
test("real audio script reads gain through playing, silent, restored, paused and resumed transport", async () => {
  const f = transportFixture();
  await f.transport.handle("play");
  for (const gain of [.65, 0, .4]) {
    const state = await f.transport.handle("volume", { gain });
    assert.equal(f.media.volume, gain);
    assert.equal(state.gain, gain);
    assert.equal(state.playing, true);
  }
  f.advance(12);
  assert.equal((await f.transport.handle("pause")).playing, false);
  const resumed = await f.transport.handle("play");
  assert.equal(resumed.playing, true);
  assert.equal(resumed.positionSeconds, 12);
  assert.equal(resumed.gain, .4);
  assert.equal((await f.transport.handle("next")).gain, .4);
  f.media.muted = true;
  assert.equal((await f.transport.handle("status")).gain, 0);
  f.media.ended = true;
  assert.equal((await f.transport.handle("status")).playing, false);
  await f.transport.handle("pause");
});
test("volume rejects coercions, nonfinite, out of bounds and extra options", () => {
  for (const gain of [NaN, Infinity, -Infinity, -.1, 1.1, "0.4", null, undefined])
    assert.throws(() => albumIntent("volume", { gain }));
  for (const options of [{}, [], { gain: .4, trackID: album.tracks[0].id }])
    assert.throws(() => albumIntent("volume", options));
});
test("resolved play is not proof of playback and rejected play never claims success", async () => {
  const f = transportFixture();
  f.media.play = async () => {};
  assert.equal((await f.transport.handle("play")).playing, false);
  f.media.play = async () => { throw new Error("play refused"); };
  await assert.rejects(f.transport.handle("play"), /play refused/);
  assert.equal(f.transport.snapshot().playing, false);
});
test("passive reads preserve cover selection and never publish; cold pause creates no window", async () => {
  const f = transportFixture();
  const before = f.transport.snapshot();
  for (const action of ["status", "catalog", "status"]) assert.deepEqual(await f.transport.handle(action), before);
  assert.equal(before.media.selectedTrackID, null);
  assert.equal(f.changes.length, 0);
  await f.transport.handle("pausePlayback");
  assert.equal(f.windows(), 0);
});
test("resume and same-track reselection preserve position; selecting another playing track plays it", async () => {
  const f = transportFixture();
  await f.transport.handle("play");
  f.advance(42);
  await f.transport.handle("pause");
  assert.equal(f.transport.snapshot().positionSeconds, 42);
  await f.transport.handle("toggle");
  await f.transport.handle("select", { trackID: album.tracks[0].id });
  assert.equal(f.calls.filter(action => action === "source").length, 1);
  assert.equal(f.transport.snapshot().playing, true);
  await f.transport.handle("select", { trackID: album.tracks[1].id });
  assert.equal(f.calls.filter(action => action === "source").length, 2);
  assert.equal(f.calls.at(-1), "play");
  assert.equal(f.transport.snapshot().media.selectedTrackID, album.tracks[1].id);
  await f.transport.handle("pause");
});
