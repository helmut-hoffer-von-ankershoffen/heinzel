// Plays the soundscape in effect once the person pressed Play (ADR 0058): a stream in its host's own player, shown in a
// small card, its own tracks in the page's audio element (ADR 0166), or layered files mixed live. Before the first Play
// it renders nothing, so nothing of another host is in the page: no frame, no audio element, no request, no cookie.
// Mounted once, beside the theme's tokens.

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { SchemaSoundscapeView } from "@/api/client";
import { useThemes } from "@/api/queries";
import { publishOwnAudio } from "@/audio/own";
import { t } from "@/i18n";
import { isIOS } from "@/lib/browser";
import { AlbumControls } from "./AlbumControls";
import { AlbumVisual } from "./AlbumVisual";
import { ALBUM_TITLE, nativeAlbum, restoreAlbumFromHost, useAlbum } from "./album";
import { setDocked } from "./dock";
import { type Inputs, rampSeconds, type Signals, targetGain, type Why, why } from "./machine";
import { type EarconView, type LayerView, Mixer } from "./mixer";
import { NativePlayer } from "./NativePlayer";
import { PlayerCard } from "./PlayerCard";
import {
  effective,
  isProgramme,
  listSignals,
  needsReload,
  page,
  readSoundList,
  rememberPagePolicy,
  SOUND_KEY,
  takeResume,
  useMeeting,
  useSoundscapes,
} from "./queries";
import { StreamPlayer } from "./StreamPlayer";
import { closeAlbum, fail, pause, resume, selectAlbum, soundState, stop, useSound } from "./store";

const SUSPENDED_MS = 1500;

/** How loud, and how fast it gets there: the gain, and the seconds of the fade from what it was. */
type Level = { gain: number; seconds: number; playing: boolean };

function useLevel(inputs: Inputs, fadeSeconds: number): Level {
  // The fade's length changes only with why the sound is as loud as it is, so a render that changes nothing else
  // doesn't restart a fade.
  const last = useRef<{ why: Why; seconds: number }>({ why: "paused", seconds: fadeSeconds });
  const now = why(inputs);
  if (last.current.why !== now) last.current = { why: now, seconds: rampSeconds(last.current.why, now, fadeSeconds) };
  return { gain: targetGain(inputs), seconds: last.current.seconds, playing: inputs.playing };
}

export function SoundEngine() {
  const list = useSoundscapes();
  const sound = useSound();
  const album = useAlbum();
  const [showTrackPicker, setShowTrackPicker] = useState(false);
  useEffect(() => {
    // Download state and a remembered title do not express playback intent.
    if (album?.playing && !soundState().albumSelected) selectAlbum();
  }, [album]);
  useEffect(() => {
    const openDock = (event: Event) => {
      if (!nativeAlbum() || !(event instanceof CustomEvent)) return;
      event.preventDefault();
      const picker = event.detail?.picker === true;
      setDocked(true);
      selectAlbum(picker);
      setShowTrackPicker(picker);
      restoreAlbumFromHost();
    };
    window.addEventListener("heinzel-album-dock", openDock);
    return () => window.removeEventListener("heinzel-album-dock", openDock);
  }, []);
  const current = effective(list.data);
  const deviceStream = isProgramme(current) && isIOS();
  const meeting = useMeeting(Boolean(list.data?.mute_in_meetings && sound.started));
  useEffect(() => {
    if (list.data) rememberPagePolicy(list.data);
  }, [list.data]);
  // A theme picked in this tab may bring its own soundscape (follow my theme): read the sound again, and load the page
  // again when its CSP must admit other origins.
  const client = useQueryClient();
  const theme = useThemes().data?.selected;
  const shownTheme = useRef(theme);
  useEffect(() => {
    if (shownTheme.current === theme) return;
    const first = shownTheme.current === undefined;
    shownTheme.current = theme;
    if (first) return;
    void client
      .fetchQuery({ queryKey: SOUND_KEY, queryFn: readSoundList, staleTime: 0 })
      .then((fresh) => needsReload(fresh) && page.reload())
      .catch(() => undefined);
  }, [theme, client]);
  // The page reloaded for a pick (its CSP origins changed): play the pick, as the click before asked.
  const listed = Boolean(list.data);
  const resumedFor = useRef(false);
  useEffect(() => {
    if (!listed || resumedFor.current) return;
    resumedFor.current = true;
    const picked = takeResume();
    if (picked && picked === current?.name) resume(picked);
  }, [listed, current?.name]);
  // Another soundscape in effect (picked elsewhere, or the theme changed): what played stops. A pick that plays is
  // the one thing Play was pressed for, so it stays.
  const name = current?.name ?? null;
  // The same soundscape with other tracks (a plugin's update, the feature switched) stops too: carrying on would
  // contact another host, perhaps YouTube, that nobody pressed Play or picked for (ADR 0166).
  const tracks = current?.tracks?.map((track) => track.url).join(" ") ?? "";
  const previous = useRef({ name, tracks });
  const renamed = previous.current.name !== name && sound.soundscape !== name;
  const retracked = previous.current.name === name && previous.current.tracks !== tracks;
  useEffect(() => {
    const was = previous.current;
    if (!soundState().albumSelected && (was.name !== name ? soundState().soundscape !== name : was.tracks !== tracks))
      stop();
    previous.current = { name, tracks };
  }, [name, tracks]);
  const level = useLevel(
    {
      playing: sound.playing,
      volume: deviceStream ? 100 : (list.data?.volume ?? 50),
      typing: sound.typing,
      voice: sound.voice,
      duckWhileTyping: !deviceStream && (list.data?.duck_while_typing ?? true),
      meeting,
      muteInMeetings: list.data?.mute_in_meetings ?? true,
    },
    current?.fade_seconds ?? 4,
  );
  const streaming = isProgramme(current) && sound.started && level.playing;
  const streamTitle = current?.title ?? "";
  // A stream plays in a cross-origin frame and tracks in an element we keep out of Web Audio (ADR 0166): neither can
  // be tapped here, so the visualizer must capture it.
  useEffect(() => {
    if (!streaming) return;
    publishOwnAudio({ mode: "frame", label: streamTitle });
    return () => publishOwnAudio({ mode: "none" });
  }, [streaming, streamTitle]);
  if (nativeAlbum() && sound.albumSelected)
    return (
      <PlayerCard
        title={ALBUM_TITLE}
        host="Latent Heinzels"
        sourceUrl="https://latentheinzels.heinzel.team/"
        artwork={<AlbumVisual album={sound.albumCover ? null : album} />}
        onClose={() => {
          setShowTrackPicker(false);
          void closeAlbum();
        }}
      >
        <AlbumControls
          showCover={sound.albumCover}
          showTrackPicker={showTrackPicker}
          onDismissTrackPicker={() => setShowTrackPicker(false)}
        />
      </PlayerCard>
    );
  if (!current || !sound.started) return null;
  // Stopping takes an effect, and tracks would play (and ask their host) in the layout effect before it.
  if (retracked || (renamed && tracks)) return null;
  if (isProgramme(current))
    return (
      <Programme
        key={current.name}
        soundscape={current}
        level={deviceStream && sound.voice ? { ...level, gain: 0 } : level}
      />
    );
  return (
    <LayeredPlayer key={current.name} soundscape={current} signals={listSignals(list.data).signals()} level={level} />
  );
}

/** Its own tracks where it has some; YouTube only where it has none, or once the person picks it. */
function Programme({ soundscape, level }: Readonly<{ soundscape: SchemaSoundscapeView; level: Level }>) {
  const [youTube, setYouTube] = useState(false);
  const { stream, tracks } = soundscape;
  if (tracks?.length && !youTube) {
    const pickYouTube = () => {
      setYouTube(true);
      resume(soundscape.name);
    };
    return (
      <NativePlayer
        soundscape={soundscape}
        tracks={tracks}
        level={level}
        pickYouTube={stream ? pickYouTube : undefined}
      />
    );
  }
  return stream ? <StreamPlayer soundscape={soundscape} stream={stream} level={level} /> : null;
}

function LayeredPlayer({
  soundscape,
  signals,
  level,
}: {
  soundscape: SchemaSoundscapeView;
  signals: Signals;
  level: Level;
}) {
  const mixer = useRef<Mixer | null>(null);
  const [loaded, setLoaded] = useState(false);
  const daypart = String(signals.daypart ?? "");
  // biome-ignore lint/correctness/useExhaustiveDependencies: built once per soundscape (the key), inside the Play click
  useEffect(() => {
    const Context =
      window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Context) {
      fail(t("sound.noAudio"));
      return;
    }
    const context = new Context();
    const created = new Mixer(context);
    mixer.current = created;
    publishOwnAudio({ mode: "analysed", analyser: created.analyser, label: soundscape.title });
    let cancelled = false; // stopped or switched while loading: what the closed context does next is no failure
    void context.resume();
    // After a reload there is no gesture yet: a context the browser keeps suspended isn't playing, so say so.
    const suspended = setTimeout(() => context.state === "suspended" && pause(), SUSPENDED_MS);
    created
      .load((soundscape.layers ?? []) as LayerView[], (soundscape.earcons ?? []) as EarconView[], signals)
      .then(() => !cancelled && setLoaded(true))
      .catch(() => !cancelled && fail(t("sound.cantLoad", { title: soundscape.title })));
    return () => {
      cancelled = true;
      clearTimeout(suspended);
      mixer.current = null;
      publishOwnAudio({ mode: "none" });
      void created.close();
    };
  }, [soundscape]);
  useEffect(() => {
    if (loaded) mixer.current?.fade(level.gain, level.seconds);
  }, [loaded, level.gain, level.seconds]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: signals change with the part of the day, read through it
  useEffect(() => {
    if (loaded) mixer.current?.follow(signals, soundscape.fade_seconds);
  }, [loaded, daypart, soundscape.fade_seconds]);
  // A notice that arrives while sound plays rings its kind's earcon, at the mix's gain (ADR 0043's `hz:notice`).
  useEffect(() => {
    const onNotice = (event: Event) => {
      const kind = (event as CustomEvent<{ kind?: string }>).detail?.kind;
      if (level.playing && kind) mixer.current?.earcon(kind);
    };
    window.addEventListener("hz:notice", onNotice);
    return () => window.removeEventListener("hz:notice", onNotice);
  }, [level.playing]);
  return null;
}
