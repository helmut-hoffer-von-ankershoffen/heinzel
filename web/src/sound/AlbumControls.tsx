import { ExternalLink, ListMusic, Pause, Play, Repeat, Repeat1, SkipBack, SkipForward, X } from "lucide-react";
import { useRef } from "react";
import { PaneAction, PaneActions } from "@/components/PaneActions";
import { Button } from "@/components/ui/button";
import { albumAction, useAlbum } from "./album";
import { stop } from "./store";

export function AlbumControls() {
  const album = useAlbum();
  const dock = useRef<HTMLElement>(null);
  const bytes = album ? `${album.receivedBytes ?? album.verifiedBytes} / ${album.totalBytes} bytes` : "";
  return (
    <section ref={dock} aria-label="Album controls" className="min-w-0 space-y-1 px-3 pb-2">
      <Button
        variant="ghost"
        size="sm"
        className="w-full min-w-0 justify-between px-0 pointer-coarse:min-h-11"
        aria-label={album?.title ? `Choose album track: ${album.title}` : "Choose album track"}
        title={album?.title || "Choose album track"}
        disabled={!album}
        onClick={() => void albumAction("picker")}
      >
        <span className="truncate">{album?.title || "Choose album track"}</span>
        <ListMusic aria-hidden />
      </Button>
      <div className="flex min-w-0 items-center justify-between gap-2">
        <PaneActions words="all" className="m-0 min-w-0 border-0 p-0">
          <PaneAction ghost icon={ExternalLink} href="https://latentheinzels.heinzel.team" className="min-w-0">
            Latent Heinzels
          </PaneAction>
        </PaneActions>
        <PaneAction
          ghost
          icon={ExternalLink}
          disabled={!album}
          onClick={() => {
            const width = dock.current?.getBoundingClientRect().width;
            void albumAction(
              "openVideo",
              width && Number.isFinite(width) ? { width, viewportWidth: window.innerWidth } : undefined,
            );
          }}
        >
          Open video
        </PaneAction>
      </div>
      <span className="sr-only" role="status">
        {album
          ? `${album.playing ? "Playing" : "Paused"} · ${album.title || "No track selected"}`
          : "Reading album status…"}
      </span>
      <fieldset className="flex min-w-0 flex-wrap items-center justify-between gap-1" aria-label="Album playback">
        <PaneAction ghost icon={SkipBack} disabled={!album} onClick={() => void albumAction("previous")}>
          Previous track
        </PaneAction>
        <PaneAction
          ghost
          icon={album?.playing ? Pause : Play}
          disabled={!album}
          onClick={() => {
            if (!album?.playing) stop();
            void albumAction(album?.title ? "toggle" : "play");
          }}
        >
          {album?.playing ? "Pause Album" : "Play Album"}
        </PaneAction>
        <PaneAction ghost icon={SkipForward} disabled={!album} onClick={() => void albumAction("next")}>
          Next track
        </PaneAction>
        <PaneAction
          ghost
          icon={album?.repeatMode === "Track" ? Repeat1 : Repeat}
          disabled={!album}
          onClick={() => void albumAction("repeat")}
        >
          {`Repeat: ${album?.repeatMode ?? "Off"}`}
        </PaneAction>
      </fieldset>
      <span className="sr-only" role="status">{`Repeat: ${album?.repeatMode ?? "Off"}`}</span>
      {album && (
        <fieldset className="flex min-w-0 items-center gap-1" aria-label="Album download status">
          <div className="min-w-0 flex-1" title={bytes}>
            <p className="text-xs text-muted-foreground tabular-nums" role="status">
              {album.phase} · {album.verified}/{album.total} verified
            </p>
            <progress
              aria-label="Album download"
              aria-valuetext={bytes}
              value={album.receivedBytes ?? album.verifiedBytes}
              max={Math.max(1, album.totalBytes)}
              className="block h-1 w-full accent-primary"
            />
          </div>
          {album.phase === "downloading" && (
            <PaneAction ghost icon={Pause} onClick={() => void albumAction("pauseDownload")}>
              Pause download
            </PaneAction>
          )}
          {["paused", "cancelled", "failed"].includes(album.phase) && (
            <PaneAction ghost icon={Play} onClick={() => void albumAction("resumeDownload")}>
              Resume download
            </PaneAction>
          )}
          {["downloading", "paused"].includes(album.phase) && (
            <PaneAction ghost icon={X} onClick={() => void albumAction("cancelDownload")}>
              Cancel download (keep verified files)
            </PaneAction>
          )}
        </fieldset>
      )}
      {album?.error && (
        <p role="alert" className="text-ui-sm text-bad">
          {album.error}
        </p>
      )}
    </section>
  );
}
