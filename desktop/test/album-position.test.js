import assert from "node:assert/strict";
import test from "node:test";
import { albumAudioPosition, seekAlbumAudio } from "../src/album-position.js";

test("seeking targets the actual album element and leaves its playback state alone", () => {
  const audio = { currentTime: 12, duration: 92, paused: true };
  assert.deepEqual(seekAlbumAudio(audio, 60), { positionSeconds: 60, durationSeconds: 92 });
  assert.equal(audio.currentTime, 60);
  assert.equal(audio.paused, true);
  assert.equal(seekAlbumAudio(audio, 100).positionSeconds, 92);
});

test("a missing or unbounded duration and invalid seek positions do not move the album element", () => {
  const audio = { currentTime: 12, duration: Infinity };
  assert.deepEqual(albumAudioPosition(audio), { positionSeconds: 12, durationSeconds: null });
  assert.throws(() => seekAlbumAudio(audio, 13), /Track position unavailable/);
  audio.duration = 92;
  for (const invalid of [-1, NaN, Infinity, "17"]) {
    assert.throws(() => seekAlbumAudio(audio, invalid), /Track position unavailable/);
  }
  assert.equal(audio.currentTime, 12);
});
