import { useCallback, useEffect, useRef, useState } from "react";
import { type AlbumSnapshot, EXTENDED_VIDEO_SHA } from "./album";

const artwork = import.meta.glob("./album-art/*.webp", { eager: true, query: "?url", import: "default" }) as Record<
  string,
  string
>;
export function albumStill(id: string | null | undefined): string | undefined {
  return artwork[`./album-art/${id || "cover"}.webp`];
}
export function approvedFilm(album: AlbumSnapshot | null): string | undefined {
  const media = album?.media;
  const film = media?.extendedVideo;
  if (
    !film ||
    film.sha256 !== EXTENDED_VIDEO_SHA ||
    film.trackID !== media?.selectedTrackID ||
    media.selectedTrackID !== "berlin-evening-extended"
  )
    return;
  try {
    const url = new URL(film.url);
    if (url.username || url.password) return;
    if (film.admission === "local" && film.url === `heinzel-album://film/${EXTENDED_VIDEO_SHA}.mp4`) return film.url;
    if (
      film.admission === "remote" &&
      url.protocol === "https:" &&
      !url.search &&
      !url.hash &&
      url.pathname.split("/").at(-1) === `${EXTENDED_VIDEO_SHA}.mp4`
    )
      return film.url;
  } catch {
    /* Unapproved media has no visible video. */
  }
}
export function AlbumVisual({ album }: { album: AlbumSnapshot | null }) {
  const id = album?.media?.selectedTrackID;
  const still = albumStill(id);
  const film = approvedFilm(album);
  const [failed, setFailed] = useState<string>();
  const video = useRef<HTMLVideoElement>(null);
  const synchronize = useCallback(() => {
    const element = video.current;
    const position = album?.positionSeconds;
    if (!element || typeof position !== "number" || !Number.isFinite(position)) return;
    const target = Math.max(0, Number.isFinite(element.duration) ? Math.min(position, element.duration) : position);
    if (Math.abs(element.currentTime - target) > 0.5) element.currentTime = target;
  }, [album?.positionSeconds]);
  useEffect(() => {
    synchronize();
  }, [synchronize]);
  useEffect(() => {
    const element = video.current;
    if (!element) return;
    synchronize();
    element.muted = true;
    if (album?.playing) void element.play().catch(() => setFailed(film));
    else element.pause();
  }, [album?.playing, film, synchronize]);
  return (
    <div className="w-full min-w-0">
      {film && failed !== film ? (
        <video
          ref={video}
          key={film}
          className="h-auto w-full object-contain"
          src={film}
          poster={still}
          muted
          playsInline
          onLoadedMetadata={synchronize}
          onError={() => setFailed(film)}
        />
      ) : still ? (
        <img
          className="aspect-video w-full object-cover"
          src={still}
          alt={id ? album?.title : "Dreaming of Electric Snow"}
        />
      ) : null}
    </div>
  );
}
