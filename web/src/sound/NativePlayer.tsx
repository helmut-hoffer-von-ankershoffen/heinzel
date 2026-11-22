// A soundscape's own tracks in the page's audio element (ADR 0166), in the same card as a stream's player. Mounted only
// after Play, so nothing is requested from the media host before. Where tracks and a YouTube stream come together,
// YouTube is contacted only when the person picks it here.
import { MonitorPlay, Pause, Play, RotateCw, SkipBack, SkipForward } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";
import type { SchemaSoundscapeView } from "@/api/client";
import { PaneAction, PaneActions } from "@/components/PaneActions";
import { t } from "@/i18n";
import { isIOS } from "@/lib/browser";
import { PlayerCard } from "./PlayerCard";
import { Playlist, type Status, type Track } from "./playlist";
import { page } from "./queries";
import { inDesktop } from "./StreamPlayer";
import { pause, resume } from "./store";

type Level = { gain: number; seconds: number; playing: boolean };
type Props = {
  soundscape: SchemaSoundscapeView;
  tracks: Track[];
  level: Level;
  /** Switch to the soundscape's YouTube stream; absent when it has none. */
  pickYouTube?: () => void;
};

/** `3:07`, or `1:02:03` from an hour on. */
export function clock(seconds: number): string {
  const two = (value: number) => String(value).padStart(2, "0");
  const minutes = Math.floor(seconds / 60);
  return minutes < 60
    ? `${minutes}:${two(seconds % 60)}`
    : `${Math.floor(minutes / 60)}:${two(minutes % 60)}:${two(seconds % 60)}`;
}

export function NativePlayer({ soundscape, tracks, level, pickYouTube }: Readonly<Props>) {
  const audio = useRef<HTMLAudioElement>(null);
  const playlist = useRef<Playlist | null>(null);
  const [status, setStatus] = useState<Status>({ index: 0, problem: null });
  const { name, title } = soundscape;
  // Layout effects: the first `play()` must still be inside the click that mounted this player, or Safari refuses it.
  // biome-ignore lint/correctness/useExhaustiveDependencies: one player per soundscape and track list (the key)
  useLayoutEffect(() => {
    if (!audio.current) return;
    const created = new Playlist(
      audio.current,
      title,
      tracks,
      { changed: setStatus, paused: pause, resumed: () => resume(name) },
      isIOS(),
    );
    playlist.current = created;
    return () => {
      playlist.current = null;
      created.dispose();
    };
  }, []);
  useLayoutEffect(() => {
    playlist.current?.level(level.gain, level.seconds, level.playing);
  }, [level.gain, level.seconds, level.playing]);

  const track = tracks[status.index];
  if (!track) return null;
  // What the card leads with: the way out of a problem, else Play or Pause.
  let lead = (
    <PaneAction key="toggle" icon={level.playing ? Pause : Play} onClick={level.playing ? pause : () => resume(name)}>
      {t(level.playing ? "sound.pauseTrack" : "sound.playTrack")}
    </PaneAction>
  );
  if (status.problem === "blocked")
    lead = (
      <PaneAction key="reload" icon={RotateCw} onClick={() => page.reload()}>
        {t("sound.reload")}
      </PaneAction>
    );
  else if (status.problem)
    lead = (
      <PaneAction key="retry" icon={RotateCw} onClick={() => playlist.current?.retry()}>
        {t("sound.retryTrack")}
      </PaneAction>
    );
  return (
    <PlayerCard
      title={title}
      host={new URL(track.url).hostname}
      watchUrl={inDesktop() ? undefined : soundscape.stream?.watch_url}
    >
      {/* biome-ignore lint/a11y/useMediaCaption: background music, with no speech to caption */}
      <audio ref={audio} hidden />
      <div className="px-3 pb-1">
        <p className="truncate text-ui-sm">{track.title}</p>
        <p className="text-xs text-muted-foreground tabular-nums">
          {t("sound.trackPosition", {
            number: status.index + 1,
            count: tracks.length,
            length: clock(track.duration_seconds),
          })}
        </p>
        {status.problem && (
          <p role="alert" className="pt-1 text-ui-sm text-bad">
            {t(status.problem === "blocked" ? "sound.tracksReloadNeeded" : "sound.trackFailed")}
          </p>
        )}
      </div>
      <PaneActions>
        {lead}
        <PaneAction key="previous" icon={SkipBack} onClick={() => playlist.current?.previous()}>
          {t("sound.previousTrack")}
        </PaneAction>
        <PaneAction key="next" icon={SkipForward} onClick={() => playlist.current?.next()}>
          {t("sound.nextTrack")}
        </PaneAction>
        {pickYouTube && (
          <PaneAction key="youtube" icon={MonitorPlay} onClick={pickYouTube}>
            {t("sound.useYouTube")}
          </PaneAction>
        )}
      </PaneActions>
    </PlayerCard>
  );
}
