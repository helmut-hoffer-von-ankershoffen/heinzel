import { ListMusic, Pause, Play, Repeat, Repeat1, SkipBack, SkipForward } from "lucide-react";
import { PaneAction } from "@/components/PaneActions";
import { Button } from "@/components/ui/button";
import { AlbumProgress } from "./AlbumProgress";
import { ALBUM_TITLE, albumAction, useAlbum, useAlbumPosition } from "./album";
import { selectAlbum } from "./store";

export function AlbumControls({
  showCover,
  showTrackPicker,
  onDismissTrackPicker,
}: {
  showCover: boolean;
  showTrackPicker: boolean;
  onDismissTrackPicker: () => void;
}) {
  const album = useAlbum();
  const trackID = album?.media?.selectedTrackID;
  const duration = album?.durationSeconds;
  const extended = trackID === "berlin-evening-extended";
  const trackTitle = extended ? album?.title.replace(/ \(Extended Version\)$/, "") : album?.title;
  useAlbumPosition(Boolean(trackID && !duration), Boolean(trackID && album?.playing));
  const choose = (id: string) => {
    onDismissTrackPicker();
    selectAlbum(!id);
    if (id) void albumAction("select", { trackID: id });
    else void albumAction("cover");
  };
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
            onChange={(event) => choose(event.target.value)}
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
          className={
            album?.repeatMode === "Album" || album?.repeatMode === "Track"
              ? "font-bold [&_svg]:stroke-[3]"
              : "[&_svg]:stroke-[1.75]"
          }
          onClick={() => void albumAction("repeat")}
        >
          {`Repeat: ${album?.repeatMode ?? "Off"}`}
        </PaneAction>
      </fieldset>
      {showTrackPicker && (
        <fieldset aria-label="Tracks" className="max-h-40 min-w-0 overflow-auto rounded border border-border">
          <div className="flex items-center justify-between px-2 py-1">
            <span className="text-sm font-semibold">Tracks</span>
            <Button type="button" variant="ghost" size="sm" onClick={onDismissTrackPicker}>
              Done
            </Button>
          </div>
          {album?.media?.tracks.map((track) => (
            <Button
              variant="ghost"
              size="sm"
              className="flex w-full whitespace-normal px-2 text-left text-sm"
              key={track.id}
              type="button"
              onClick={() => choose(track.id)}
            >
              {track.title}
            </Button>
          ))}
        </fieldset>
      )}
      <span className="sr-only" role="status">{`Repeat: ${album?.repeatMode ?? "Off"}`}</span>
      {album?.error && (
        <p role="alert" className="text-ui-sm text-bad">
          {album.error}
        </p>
      )}
    </section>
  );
}
