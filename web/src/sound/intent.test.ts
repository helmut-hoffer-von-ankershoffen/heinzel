import { afterEach, expect, it, vi } from "vitest";
import { type AlbumSnapshot, albumAction, albumState } from "./album";
import { closeAlbum, resetSound, selectAlbum, soundState } from "./store";

const active: AlbumSnapshot = {
  title: "Test album",
  playing: true,
  phase: "ready",
  verified: 0,
  total: 0,
  verifiedBytes: 0,
  totalBytes: 0,
  error: null,
  repeatMode: "Off",
};
afterEach(() => {
  resetSound();
  vi.unstubAllGlobals();
});

it.each(["resolve", "reject"])(
  "closes before an asynchronous pause can %s and rejects subsequent active reports",
  async (outcome) => {
    let resolve!: (value: AlbumSnapshot) => void;
    let reject!: () => void;
    const pending = new Promise<AlbumSnapshot>((yes, no) => {
      resolve = yes;
      reject = no;
    });
    vi.stubGlobal("heinzelDesktop", {
      album: vi.fn((action: string) => (action === "pausePlayback" ? pending : Promise.resolve(active))),
    });
    selectAlbum();
    await albumAction("play");
    const closing = closeAlbum();
    expect(soundState().albumSelected).toBe(false);
    expect(albumState()?.playing).toBe(false);
    if (outcome === "resolve") resolve(active);
    else reject();
    await closing;
    await albumAction("status");
    expect(albumState()?.playing).toBe(false);
    expect(soundState().albumSelected).toBe(false);
    selectAlbum();
    await albumAction("play");
    expect(albumState()?.playing).toBe(true);
  },
);

it("invalidates an active report already in flight at Off", async () => {
  let resolve!: (value: AlbumSnapshot) => void;
  vi.stubGlobal("heinzelDesktop", {
    album: (action: string) =>
      action === "status"
        ? new Promise<AlbumSnapshot>((done) => {
            resolve = done;
          })
        : Promise.resolve(active),
  });
  selectAlbum();
  const reading = albumAction("status");
  await closeAlbum();
  resolve(active);
  expect(await reading).toBeUndefined();
  expect(albumState()?.playing).toBe(false);
});

it("keeps an in-flight selection authoritative over passive status", async () => {
  let resolve!: (value: AlbumSnapshot) => void;
  const bridge = vi.fn((action: string) =>
    action === "select"
      ? new Promise<AlbumSnapshot>((done) => {
          resolve = done;
        })
      : Promise.resolve(active),
  );
  vi.stubGlobal("heinzelDesktop", { album: bridge });
  const choosing = albumAction("select", { trackID: "berlin-morning" });
  await albumAction("status");
  expect(bridge).toHaveBeenCalledTimes(1);
  resolve(active);
  await choosing;
  expect(albumState()).toEqual(active);
});

it("chooses the cover explicitly and clears old film while a new track is pending", async () => {
  const old = {
    ...active,
    media: { albumID: "snow", selectedTrackID: "berlin-evening-extended", tracks: [], extendedVideo: null },
  };
  let resolve!: (value: AlbumSnapshot) => void;
  vi.stubGlobal("heinzelDesktop", {
    album: (action: string) =>
      action === "select"
        ? new Promise<AlbumSnapshot>((done) => {
            resolve = done;
          })
        : Promise.resolve(old),
  });
  await albumAction("status");
  selectAlbum();
  expect(soundState().albumCover).toBe(true);
  selectAlbum(false);
  const selecting = albumAction("select", { trackID: "berlin-morning" });
  expect(soundState().albumCover).toBe(false);
  expect(albumState()?.media?.selectedTrackID).toBe("berlin-morning");
  expect(albumState()?.media?.extendedVideo).toBeNull();
  resolve({ ...old, media: { ...old.media, selectedTrackID: "berlin-morning" } });
  await selecting;
});
