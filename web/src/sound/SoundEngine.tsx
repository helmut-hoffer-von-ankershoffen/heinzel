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
import { PlayerCard, useDocked } from "./PlayerCard";
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
import { fail, stop, useSound } from "./store";
import { command, embedSrc, FRAME_ATTRIBUTES, listen, playerEvent, type StreamView } from "./stream";

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

const STEP_MS = 100;

function StreamPlayer({
  soundscape,
  stream,
  level,
}: {
  soundscape: SchemaSoundscapeView;
  stream: StreamView;
  level: Level;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const volume = useRef(0);
  const src = embedSrc(stream);
  const origin = stream.frame_origin;
  // Docking or popping out puts the frame somewhere else in the page: a new frame, which says it is ready again.
  const docked = useDocked();
  // biome-ignore lint/correctness/useExhaustiveDependencies: a change of place is the trigger
  useEffect(() => setReady(false), [docked]);
  useEffect(() => {
    const onMessage = (message: MessageEvent) => {
      const event = playerEvent(message, frame.current, origin);
      if (event !== "onReady" && event !== "initialDelivery") return;
      // Silent first, so the fade-in starts from nothing, never at the player's own volume.
      command(frame.current, origin, "setVolume", [volume.current]);
      setReady(true);
    };
    // The page's CSP admits no frame of this host (the soundscape changed elsewhere): it must load again.
    const onViolation = (event: SecurityPolicyViolationEvent) => {
      if (event.effectiveDirective === "frame-src" && event.blockedURI.startsWith(origin)) setBlocked(true);
    };
    window.addEventListener("message", onMessage);
    document.addEventListener("securitypolicyviolation", onViolation);
    return () => {
      window.removeEventListener("message", onMessage);
      document.removeEventListener("securitypolicyviolation", onViolation);
    };
  }, [origin]);
  // Fade by steps of setVolume; at 0 after a pause the video pauses too, and plays again before fading in.
  useEffect(() => {
    if (!ready) return;
    const target = Math.round(level.gain * 100);
    if (level.playing) command(frame.current, origin, "playVideo");
    const steps = Math.max(1, Math.round((level.seconds * 1000) / STEP_MS));
    const start = volume.current;
    let step = 0;
    const timer = setInterval(() => {
      step += 1;
      volume.current = Math.round(start + ((target - start) * step) / steps);
      command(frame.current, origin, "setVolume", [volume.current]);
      if (step >= steps) {
        clearInterval(timer);
        if (!level.playing) command(frame.current, origin, "pauseVideo");
      }
    }, STEP_MS);
    return () => clearInterval(timer);
  }, [ready, level.gain, level.seconds, level.playing, origin]);
  if (!src) return null;
  return (
    <PlayerCard title={soundscape.title} watchUrl={stream.watch_url} host="YouTube">
      {blocked ? (
        <div className="flex flex-col items-start gap-2 p-3 text-ui-sm">
          <p>{t("sound.reloadNeeded")}</p>
          <button
            type="button"
            onClick={page.reload}
            className="rounded-lg bg-primary px-2.5 py-1 text-primary-foreground"
          >
            {t("sound.reload")}
          </button>
        </div>
      ) : (
        <iframe
          ref={frame}
          title={t("sound.player", { title: soundscape.title })}
          src={src}
          onLoad={() => listen(frame.current, origin)}
          className="aspect-video w-full border-0"
          referrerPolicy={FRAME_ATTRIBUTES.referrerPolicy}
          sandbox={FRAME_ATTRIBUTES.sandbox}
          allow={FRAME_ATTRIBUTES.allow}
        />
      )}
    </PlayerCard>
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
