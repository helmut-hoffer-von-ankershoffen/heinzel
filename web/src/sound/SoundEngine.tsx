// Plays the soundscape in effect once the person pressed Play (ADR 0058): a stream in its host's own player, shown in a
// small card, or layered files mixed live. Before the first Play it renders nothing, so nothing of a stream's host is
// in the page: no frame, no request, no cookie. Mounted once, beside the theme's tokens.
import { ExternalLink, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { SchemaSoundscapeView } from "@/api/client";
import { t } from "@/i18n";
import { type Inputs, rampSeconds, type Signals, targetGain, type Why, why } from "./machine";
import { type EarconView, type LayerView, Mixer } from "./mixer";
import { effective, listSignals, page, rememberPageFrame, useMeeting, useSoundscapes } from "./queries";
import { fail, stop, useSound } from "./store";
import { command, embedSrc, FRAME_ATTRIBUTES, listen, playerEvent, type StreamView } from "./stream";

/** How loud, and how fast it gets there: the gain, and the seconds of the fade from what it was. */
type Level = { gain: number; seconds: number; playing: boolean };

function useLevel(inputs: Inputs, fadeSeconds: number): Level {
  const before = useRef<Why>("paused");
  const now = why(inputs);
  const seconds = rampSeconds(before.current, now, fadeSeconds);
  useEffect(() => {
    before.current = now;
  }, [now]);
  return { gain: targetGain(inputs), seconds, playing: inputs.playing };
}

export function SoundEngine() {
  const list = useSoundscapes();
  const sound = useSound();
  const current = effective(list.data);
  const meeting = useMeeting(Boolean(list.data?.mute_in_meetings && sound.started));
  useEffect(() => {
    if (list.data) rememberPageFrame(list.data);
  }, [list.data]);
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
  useEffect(() => {
    const onMessage = (message: MessageEvent) => {
      const event = playerEvent(message, frame.current, origin);
      if (event === "onReady" || event === "initialDelivery") setReady(true);
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
    <section
      aria-label={t("sound.player", { title: soundscape.title })}
      className="fixed bottom-[68px] left-3 z-40 w-72 overflow-hidden rounded-xl border border-border bg-popover shadow-xl"
    >
      <header className="flex items-center gap-2 px-3 py-1.5">
        <div className="min-w-0 flex-1">
          <p className="truncate text-ui-sm font-medium">{soundscape.title}</p>
          <p className="truncate text-xs text-muted-foreground">{t("sound.playsFrom", { host: "YouTube" })}</p>
        </div>
        <a
          href={stream.watch_url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={t("sound.openOn", { host: "YouTube" })}
          title={t("sound.openOn", { host: "YouTube" })}
          className="flex size-7 items-center justify-center rounded-lg text-icon hover:bg-accent"
        >
          <ExternalLink aria-hidden className="size-3.5" />
        </a>
        <button
          type="button"
          onClick={stop}
          aria-label={t("sound.close")}
          title={t("sound.close")}
          className="flex size-7 items-center justify-center rounded-lg text-icon hover:bg-accent"
        >
          <X aria-hidden className="size-3.5" />
        </button>
      </header>
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
    </section>
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
    void context.resume();
    created
      .load((soundscape.layers ?? []) as LayerView[], (soundscape.earcons ?? []) as EarconView[], signals)
      .then(() => setLoaded(true))
      .catch(() => fail(t("sound.cantLoad", { title: soundscape.title })));
    return () => {
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
