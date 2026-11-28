import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { AlbumVisual, albumStill, approvedFilm } from "./AlbumVisual";
import { type AlbumSnapshot, EXTENDED_VIDEO_SHA } from "./album";

const snapshot = (id: string): AlbumSnapshot => ({
  phase: "ready",
  verified: 37,
  total: 37,
  verifiedBytes: 1,
  totalBytes: 1,
  error: null,
  title: id,
  playing: false,
  repeatMode: "Off",
  media: { albumID: "snow", selectedTrackID: id, tracks: [], extendedVideo: null },
});
it("uses only the selected bundled still, with the cover for album selection", () => {
  expect(albumStill(null)).toContain("cover.webp");
  expect(albumStill("berlin-morning")).toContain("berlin-morning.webp");
  expect(albumStill("unknown")).toBeUndefined();
});
it("never admits a standard video", () => {
  const album = snapshot("berlin-morning");
  if (!album.media) throw new Error("Missing fixture media");
  album.media.extendedVideo = {
    url: `heinzel-album://film/${EXTENDED_VIDEO_SHA}.mp4`,
    sha256: EXTENDED_VIDEO_SHA,
    trackID: "berlin-morning",
    admission: "local",
  };
  expect(approvedFilm(album)).toBeUndefined();
});
it("fails to the selected extended still", () => {
  const album = snapshot("berlin-evening-extended");
  if (!album.media) throw new Error("Missing fixture media");
  album.media.extendedVideo = {
    url: `heinzel-album://film/${EXTENDED_VIDEO_SHA}.mp4`,
    sha256: EXTENDED_VIDEO_SHA,
    trackID: "berlin-evening-extended",
    admission: "local",
  };
  const view = render(<AlbumVisual album={album} />);
  fireEvent.error(requiredVideo(view.container));
  expect(screen.getByRole("img")).toHaveAttribute("src", albumStill("berlin-evening-extended"));
  expect(view.container.querySelector("video")).toBeNull();
});

it("keeps the silent film synchronized to native play and pause", () => {
  const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  const album = snapshot("berlin-evening-extended");
  if (!album.media) throw new Error("Missing fixture media");
  album.media.extendedVideo = {
    url: `heinzel-album://film/${EXTENDED_VIDEO_SHA}.mp4`,
    sha256: EXTENDED_VIDEO_SHA,
    trackID: "berlin-evening-extended",
    admission: "local",
  };
  const view = render(<AlbumVisual album={album} />);
  expect(pause).toHaveBeenCalled();
  view.rerender(<AlbumVisual album={{ ...album, playing: true }} />);
  expect(play).toHaveBeenCalledTimes(1);
  expect(requiredVideo(view.container).muted).toBe(true);
  pause.mockClear();
  view.rerender(<AlbumVisual album={{ ...album, playing: false }} />);
  expect(pause).toHaveBeenCalledTimes(1);
  play.mockRestore();
  pause.mockRestore();
});

it("tracks finite host time on metadata, resume and drift without looping or jitter seeks", () => {
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  const album = snapshot("berlin-evening-extended");
  if (!album.media) throw new Error("Missing fixture media");
  album.media.extendedVideo = {
    url: `heinzel-album://film/${EXTENDED_VIDEO_SHA}.mp4`,
    sha256: EXTENDED_VIDEO_SHA,
    trackID: "berlin-evening-extended",
    admission: "local",
  };
  const view = render(<AlbumVisual album={{ ...album, positionSeconds: 30 }} />);
  const video = requiredVideo(view.container);
  video.currentTime = 0;
  fireEvent.loadedMetadata(video);
  expect(video.currentTime).toBe(30);
  expect(video.loop).toBe(false);
  view.rerender(<AlbumVisual album={{ ...album, positionSeconds: 30.2 }} />);
  expect(video.currentTime).toBe(30);
  view.rerender(<AlbumVisual album={{ ...album, playing: true, positionSeconds: 80 }} />);
  expect(video.currentTime).toBe(80);
  view.rerender(<AlbumVisual album={{ ...album, playing: true, positionSeconds: Number.NaN }} />);
  expect(video.currentTime).toBe(80);
  vi.restoreAllMocks();
});

function requiredVideo(container: HTMLElement): HTMLVideoElement {
  const video = container.querySelector("video");
  if (!video) throw new Error("Missing video");
  return video;
}
