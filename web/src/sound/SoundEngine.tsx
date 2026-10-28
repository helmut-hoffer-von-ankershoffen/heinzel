// Plays the soundscape in effect once the person pressed Play (ADR 0058): a stream in its host's own player, shown in a
// small card, or layered files mixed live. Before the first Play it renders nothing, so nothing of a stream's host is
// in the page: no frame, no request, no cookie. Mounted once, beside the theme's tokens.

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { SchemaSoundscapeView } from "@/api/client";
import { useThemes } from "@/api/queries";
import { t } from "@/i18n";
import { type Inputs, rampSeconds, type Signals, targetGain, type Why, why } from "./machine";
import { type EarconView, type LayerView, Mixer } from "./mixer";
import {
  effective,
  listSignals,
  needsReload,
  page,
  readSoundList,
  rememberPageFrame,
  SOUND_KEY,
  useMeeting,
  useSoundscapes,
} from "./queries";
import { StreamPlayer } from "./StreamPlayer";
import { fail, stop, useSound } from "./store";

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
  const current = effective(list.data);
  const meeting = useMeeting(Boolean(list.data?.mute_in_meetings && sound.started));
  useEffect(() => {
    if (list.data) rememberPageFrame(list.data);
  }, [list.data]);
  // A theme picked in this tab may bring its own soundscape (follow my theme): read the sound again, and load the page
  // again when its CSP must admit another frame origin.
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
  // Another soundscape in effect (picked elsewhere, or the theme changed): what played stops.
  const name = current?.name ?? null;
  const previous = useRef(name);
  useEffect(() => {
    if (previous.current !== name) stop();
    previous.current = name;
  }, [name]);
  const level = useLevel(
    {
      playing: sound.playing,
      volume: list.data?.volume ?? 50,
      typing: sound.typing,
      voice: sound.voice,
      duckWhileTyping: list.data?.duck_while_typing ?? true,
      meeting,
      muteInMeetings: list.data?.mute_in_meetings ?? true,
    },
    current?.fade_seconds ?? 4,
  );
  if (!current || !sound.started) return null;
  if (current.stream)
    return <StreamPlayer key={current.name} soundscape={current} stream={current.stream} level={level} />;
  return (
    <LayeredPlayer key={current.name} soundscape={current} signals={listSignals(list.data).signals()} level={level} />
  );
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
    let cancelled = false; // stopped or switched while loading: what the closed context does next is no failure
    void context.resume();
    created
      .load((soundscape.layers ?? []) as LayerView[], (soundscape.earcons ?? []) as EarconView[], signals)
      .then(() => !cancelled && setLoaded(true))
      .catch(() => !cancelled && fail(t("sound.cantLoad", { title: soundscape.title })));
    return () => {
      cancelled = true;
      mixer.current = null;
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
