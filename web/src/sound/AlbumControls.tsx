import { ListMusic, Pause, Play, Repeat, Repeat1, SkipBack, SkipForward } from "lucide-react";
import { PaneAction } from "@/components/PaneActions";
import { AlbumProgress } from "./AlbumProgress";
import { ALBUM_TITLE, albumAction, useAlbum, useAlbumPosition } from "./album";
import { selectAlbum } from "./store";

export function AlbumControls({ showCover }: { showCover: boolean }) {
  const album = useAlbum();
  const trackID = album?.media?.selectedTrackID;
  const duration = album?.durationSeconds;
  const extended = trackID === "berlin-evening-extended";
  const trackTitle = extended ? album?.title.replace(/ \(Extended Version\)$/, "") : album?.title;
  useAlbumPosition(Boolean(trackID && !duration), Boolean(trackID && album?.playing));
  return (
    <section aria-label="Album controls" className="min-w-0 space-y-1 px-3 pb-2">
      {trackID && trackTitle && !showCover && (
        <div>
          <p className="truncate text-sm font-semibold">{trackTitle}</p>
          {extended && <p className="text-xs text-muted-foreground">Extended Version</p>}
        </div>
      )}
      <span className="sr-only" role="status">
        {album
          ? `${album.playing ? "Playing" : "Paused"} · ${album.title || "No track selected"}`
          : "Reading album status…"}
      </span>
      {album && (
        <AlbumProgress
          key={trackID}
          transfer={album}
          position={duration ? { elapsedSeconds: album.positionSeconds ?? 0, durationSeconds: duration } : undefined}
          onSeek={(seconds) => {
            if (trackID) void albumAction("seek", { trackID, seconds });
          }}
          onTransfer={(action) => void albumAction(action)}
        />
      )}
      <fieldset className="flex min-w-0 flex-wrap items-center justify-between gap-1" aria-label="Album playback">
        <PaneAction ghost icon={SkipBack} disabled={!album} onClick={() => void albumAction("previous")}>
          Previous track
        </PaneAction>
        <PaneAction
          ghost
          icon={album?.playing ? Pause : Play}
          disabled={!album}
          onClick={() => {
            if (!album?.playing) selectAlbum(false);
            void albumAction(album?.title ? "toggle" : "play");
          }}
        >
          {album?.playing ? "Pause Album" : "Play Album"}
        </PaneAction>
        <PaneAction ghost icon={SkipForward} disabled={!album} onClick={() => void albumAction("next")}>
          Next track
        </PaneAction>
        <label
          className="relative grid size-8 place-items-center rounded-md hover:bg-accent focus-within:ring-2 focus-within:ring-ring"
          title="Choose album track"
        >
          <ListMusic aria-hidden size={16} />
          <select
            aria-label="Choose album track"
            className="absolute inset-0 size-full cursor-pointer opacity-0 disabled:cursor-default"
            value={showCover ? "" : (trackID ?? "")}
            disabled={!album?.media?.tracks.length}
            onPointerDown={() => void albumAction("catalog")}
            onKeyDown={() => void albumAction("catalog")}
            onChange={(event) => {
              const selected = event.target.value;
              selectAlbum(!selected);
              if (selected) void albumAction("select", { trackID: selected });
            }}
          >
            <option value="">{ALBUM_TITLE} · Album</option>
            {album?.media?.tracks.map((track) => (
              <option key={track.id} value={track.id}>
                {track.title}
              </option>
            ))}
          </select>
        </label>
        <PaneAction
          ghost
          icon={album?.repeatMode === "Track" ? Repeat1 : Repeat}
          disabled={!album}
          className={album?.repeatMode === "Off" ? "[&_svg]:stroke-[1.75]" : "font-bold [&_svg]:stroke-[3]"}
          aria-pressed={album?.repeatMode !== "Off"}
          onClick={() => void albumAction("repeat")}
        >
          {`Repeat: ${album?.repeatMode ?? "Off"}`}
        </PaneAction>
      </fieldset>
      <span className="sr-only" role="status">{`Repeat: ${album?.repeatMode ?? "Off"}`}</span>
      {album?.error && (
        <p role="alert" className="text-ui-sm text-bad">
          {album.error}
        </p>
      )}
    </section>
  );
}
